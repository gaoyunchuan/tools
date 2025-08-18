#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import re
import sys
import os
import glob
from typing import Set, List, Dict, Any
from typing_extensions import Annotated

try:
    import yaml
except ImportError:
    print("❌ 错误: 未找到 PyYAML 库。请先通过 'pip install pyyaml' 命令安装它。")
    sys.exit(1)

try:
    import typer
except ImportError:
    print("❌ 错误: 未找到 Typer 库。请先通过 'pip install \"typer[all]\"' 命令安装它。")
    sys.exit(1)


# --- App Definition ---
app = typer.Typer(
    help="一键式转换工具：将一个公网 Helm Chart 转换为离线部署包。",
    add_completion=False,
    rich_markup_mode="markdown"
)

# --- Core Logic Functions (Unchanged) ---

# 用于从 Helm 模板输出中匹配镜像名称的正则表达式
IMAGE_REGEX = re.compile(r'image:\s*["\']?([a-zA-Z0-9-./_:@]+)["\']?')


def run_command(command: List[str], capture_output=True) -> str:
    """
    执行一个 shell 命令并返回其输出。如果命令执行失败，则打印错误并退出程序。
    """
    print(f"🔩 正在执行: {' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=capture_output,
            text=True,
            encoding='utf-8'
        )
        if capture_output:
            return result.stdout
        return ""
    except FileNotFoundError:
        print(f"❌ 错误: 命令 '{command[0]}' 未找到。请确认它是否已安装并在系统的 PATH 路径中。")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"❌ 命令执行失败: {' '.join(command)}")
        print(f"   错误输出: {e.stderr}")
        sys.exit(1)


def get_images_from_chart(chart_name: str, chart_version: str = None) -> Set[str]:
    """
    通过渲染 Helm Chart 模板来提取其中所有唯一的镜像名称。
    """
    print(f"\n🔍 步骤 1: 在 Chart '{chart_name}' 中查找所有容器镜像...")
    command = ["helm", "template", "release-name-placeholder", chart_name]
    if chart_version:
        command.extend(["--version", chart_version])
    template_output = run_command(command)
    found_images = set(IMAGE_REGEX.findall(template_output))
    
    if not found_images:
        print(f"⚠️ 警告: 在 Chart '{chart_name}' 中没有找到任何镜像。")
    else:
        print(f"✅ 成功找到 {len(found_images)} 个唯一镜像。")
    return found_images


def process_image(original_image: str, private_registry: str):
    """
    拉取、重新标记并推送单个镜像到私有仓库。
    """
    print(f"\n🔄 正在处理镜像: {original_image}")
    # 构造新的镜像标签，只取原始镜像名的最后一部分
    image_name_part = original_image.split('/')[-1]
    new_image_tag = f"{private_registry}/{image_name_part}"

    print(f"   -> 拉取 '{original_image}'...")
    run_command(["docker", "pull", original_image], capture_output=False)
    print(f"   -> 标记为 '{new_image_tag}'...")
    run_command(["docker", "tag", original_image, new_image_tag], capture_output=False)
    print(f"   -> 推送至 '{new_image_tag}'...")
    run_command(["docker", "push", new_image_tag], capture_output=False)
    print(f"   ✅ 成功处理 '{original_image}'")


def generate_offline_values(chart_name: str, private_registry: str, output_dir: str, chart_version: str = None):
    """
    获取 Chart 的默认 values.yaml，并智能地生成一个指向私有仓库的 offline-values.yaml。
    """
    print(f"\n📝 步骤 3: 正在生成 offline-values.yaml 文件...")
    
    command = ["helm", "show", "values", chart_name]
    if chart_version:
        command.extend(["--version", chart_version])
    
    original_values_str = run_command(command)
    original_values = yaml.safe_load(original_values_str)
    
    offline_values = {}

    def find_and_update_images(data: Any, path: List[str]):
        if isinstance(data, dict):
            if 'repository' in data and 'tag' in data:
                current_node = offline_values
                for key in path:
                    current_node = current_node.setdefault(key, {})
                current_node['registry'] = private_registry
                current_node['repository'] = data['repository'].split('/')[-1]
            for key, value in data.items():
                find_and_update_images(value, path + [key])
        elif isinstance(data, list):
            for i, item in enumerate(data):
                find_and_update_images(item, path + [i])
    
    find_and_update_images(original_values, [])
    
    if 'global' not in offline_values:
        offline_values['global'] = {}
    offline_values['global']['imageRegistry'] = private_registry
    
    values_file_path = os.path.join(output_dir, "offline-values.yaml")
    with open(values_file_path, 'w', encoding='utf-8') as f:
        yaml.dump(offline_values, f, default_flow_style=False, sort_keys=False, indent=2, allow_unicode=True)
        
    print(f"✅ 成功生成 '{values_file_path}'。")


# --- Typer Command ---
@app.command()
def run(
    chart: Annotated[str, typer.Argument(
        help="Helm Chart 的名称 (例如: 'kubeshark/kubeshark')"
    )],
    registry: Annotated[str, typer.Argument(
        help="您的私有镜像仓库地址 (例如: 'your-registry.com/my-project')"
    )],
    version: Annotated[str, typer.Option(
        "--version",
        help="要处理的 Chart 的特定版本 (推荐指定)"
    )] = None,
    namespace: Annotated[str, typer.Option(
        "-n", "--namespace",
        help="为最终部署命令指定的目标命名空间"
    )] = "default"
):
    """
    一键式转换工具：将一个公网 Helm Chart 转换为离线部署包。
    """
    private_registry = registry.rstrip('/')
    chart_simple_name = chart.split('/')[-1]

    run_command(["helm", "repo", "update"], capture_output=False)
    
    images_to_process = get_images_from_chart(chart, version)
    if not images_to_process:
        print("\n没有找到需要处理的镜像，程序退出。")
        raise typer.Exit()

    print("\n🚀 步骤 2: 开始迁移所有镜像至您的私有仓库...")
    for image in sorted(list(images_to_process)):
        process_image(image, private_registry)

    version_suffix = f"-{version}" if version else ""
    output_dir = f"./build/{chart_simple_name}{version_suffix}-offline"
    os.makedirs(output_dir, exist_ok=True)
    print(f"\n📦 已创建输出目录: {output_dir}")

    generate_offline_values(chart, private_registry, output_dir, version)
    
    print("\n📥 步骤 4: 正在下载 Helm Chart 包...")
    fetch_command = ["helm", "fetch", chart, "--destination", output_dir]
    if version:
        fetch_command.extend(["--version", version])
    run_command(fetch_command, capture_output=False)
    
    chart_tgz_list = glob.glob(os.path.join(output_dir, f"{chart_simple_name}-*.tgz"))
    if not chart_tgz_list:
        print(f"❌ 错误: 在目录 {output_dir} 中找不到下载的 Chart 包。")
        raise typer.Exit(code=1)
    chart_tgz = os.path.basename(chart_tgz_list[0])
    print(f"✅ 成功下载 '{chart_tgz}'")
    
    release_name = chart_simple_name
    deployment_command = (
        f"helm install {release_name} ./{chart_tgz} \\\n"
        f"  -f ./offline-values.yaml \\\n"
        f"  --namespace {namespace} --create-namespace"
    )
    
    # --- Final Summary ---
    summary = f"""
[bold green]🎉 恭喜！一键转换完成！ 🎉[/bold green]

所有容器镜像均已推送至您的私有仓库。
部署所需的所有文件都已保存在以下目录中:
  [yellow]'{output_dir}/'[/yellow]

下一步，请将整个文件夹传输到您的离线环境中，然后执行以下命令进行部署:

--- [bold cyan]离线环境部署命令[/bold cyan] ---
[bold]cd {output_dir}[/bold]
[bold]{deployment_command}[/bold]
--------------------------
    """
    typer.echo(summary)


if __name__ == "__main__":
    app()
