#!/usr/bin/env python3
"""Interactive, format-preserving INI update tool."""

from __future__ import annotations

import glob
import os
import readline
import re
import subprocess
import sys
import tempfile
import shutil
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DEFAULT_CUSTOMER_SETTINGS = Path("/opt/engconfig/settings.ini")
DEFAULT_NEW_SETTINGS = Path("/opt/engfur/engconfig/settings.ini")
SECTION_PATTERN = re.compile(r"^\s*\[([^\]]+)\]\s*$")
SETTING_PATTERN = re.compile(r"^\s*([^#;\s][^=]*?)\s*=(.*)$")


@dataclass
class Setting:
    name: str
    line: str
    body_index: int


@dataclass
class Section:
    name: str
    header_line: str
    body_lines: list[str] = field(default_factory=list)
    settings: OrderedDict[str, Setting] = field(default_factory=OrderedDict)

    def add_body_line(self, line: str) -> None:
        self.body_lines.append(line)
        match = SETTING_PATTERN.match(line.rstrip("\r\n"))
        if match:
            setting_name = match.group(1).strip()
            self.settings[setting_name] = Setting(setting_name, line, len(self.body_lines) - 1)


@dataclass
class IniDocument:
    prefix_lines: list[str]
    sections: OrderedDict[str, Section]


def read_document(path: Path) -> IniDocument:
    with path.open("r", encoding="utf-8", errors="surrogateescape", newline="") as source_file:
        lines = source_file.readlines()

    prefix_lines: list[str] = []
    sections: OrderedDict[str, Section] = OrderedDict()
    current_section: Section | None = None

    for line in lines:
        section_match = SECTION_PATTERN.match(line.rstrip("\r\n"))
        if section_match:
            section_name = section_match.group(1)
            current_section = Section(section_name, line)
            sections[section_name] = current_section
        elif current_section is None:
            prefix_lines.append(line)
        else:
            current_section.add_body_line(line)

    return IniDocument(prefix_lines, sections)


def display_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1 for character in value)


def terminal_width() -> int:
    return max(64, min(shutil.get_terminal_size(fallback=(96, 24)).columns - 2, 110))


def print_panel(title: str, lines: list[str], width: int | None = None) -> None:
    width = width or terminal_width()
    inner_width = width - 2
    title_width = display_width(title)
    left = max(1, (inner_width - title_width - 2) // 2)
    right = max(1, inner_width - title_width - left - 2)
    print("╔" + "═" * left + f" {title} " + "═" * right + "╗")
    for line in lines:
        logical_lines = line.splitlines() or [""]
        for logical_line in logical_lines:
            for wrapped_line in wrap_text(logical_line, inner_width - 2):
                print(f"║ {wrapped_line}{' ' * (inner_width - 2 - display_width(wrapped_line))} ║")
    print("╚" + "═" * inner_width + "╝")


def clear_current_screen() -> None:
    """清空当前可见终端区域，但保留终端的滚动历史。"""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def prompt_choice(prompt: str, valid_choices: set[str]) -> str:
    while True:
        print_panel("请输入选择", [prompt])
        choice = input("  └─> ").strip()
        if choice in valid_choices:
            clear_current_screen()
            return choice
        print_panel("输入无效", ["请输入允许的选项后再试一次。"])


def choose_category(title: str, first_action: str, second_action: str) -> str:
    print_panel(title, [
        f"【1】{first_action}        【2】{second_action}        【3】逐项询问",
    ])
    return {"1": "first", "2": "second", "3": "per_item"}[prompt_choice("请选择 [1-3]：", {"1", "2", "3"})]


def wrap_text(value: str, width: int) -> list[str]:
    lines: list[str] = []
    current_line = ""
    for character in value:
        if current_line and display_width(current_line + character) > width:
            lines.append(current_line)
            current_line = character
        else:
            current_line += character
    if current_line or not lines:
        lines.append(current_line)
    return lines


def wrap_configuration_line(value: str, width: int) -> list[str]:
    if "=" not in value:
        return wrap_text(value, width)

    setting_name, setting_value = value.split("=", 1)
    first_prefix = setting_name + "="
    if display_width(first_prefix) >= width:
        return wrap_text(value, width)

    lines: list[str] = []
    current_line = first_prefix
    continuation_prefix = " " * display_width(first_prefix)
    for character in setting_value:
        if display_width(current_line + character) > width:
            lines.append(current_line)
            current_line = continuation_prefix + character
        else:
            current_line += character
    lines.append(current_line)
    return lines


def wrap_configuration(value: str, width: int) -> list[str]:
    lines: list[str] = []
    for configuration_line in value.splitlines() or [value]:
        lines.extend(wrap_configuration_line(configuration_line, width))
    return lines


def print_comparison(title: str, details: list[str]) -> None:
    context = [detail for detail in details if not detail.startswith(("旧配置：", "新配置："))]
    old_value = next((detail.removeprefix("旧配置：") for detail in details if detail.startswith("旧配置：")), "（旧配置中不存在）")
    new_value = next((detail.removeprefix("新配置：") for detail in details if detail.startswith("新配置：")), "（新版配置中不存在）")
    width = terminal_width()
    if width < 76:
        content_width = width - 4
        print_panel(title, [
            *context,
            "客户旧配置：",
            *wrap_configuration(old_value, content_width),
            "新版配置：",
            *wrap_configuration(new_value, content_width),
        ], width)
        return

    column_width = (width - 7) // 2
    content_width = column_width - 2
    old_lines = wrap_configuration(old_value, content_width)
    new_lines = wrap_configuration(new_value, content_width)
    row_count = max(len(old_lines), len(new_lines))

    print_panel(title, context, width)
    print("┌" + "─" * column_width + "┐  ┌" + "─" * column_width + "┐")
    old_title = "客户旧配置"
    new_title = "新版配置"
    print(f"│ {old_title}{' ' * (content_width - display_width(old_title))} │  │ {new_title}{' ' * (content_width - display_width(new_title))} │")
    for row_index in range(row_count):
        old_line = old_lines[row_index] if row_index < len(old_lines) else ""
        new_line = new_lines[row_index] if row_index < len(new_lines) else ""
        print(f"│ {old_line}{' ' * (content_width - display_width(old_line))} │  │ {new_line}{' ' * (content_width - display_width(new_line))} │")
    print("└" + "─" * column_width + "┘  └" + "─" * column_width + "┘")


def choose_per_item(title: str, details: list[str], first_action: str, second_action: str) -> str:
    print()
    print_comparison(title, details)
    print_panel("处理方式", [f"【1】{first_action}                              【2】{second_action}"])
    return {"1": "first", "2": "second"}[prompt_choice("请选择 [1-2]：", {"1", "2"})]


def section_content(section: Section) -> str:
    configuration_lines = [setting.line.rstrip("\r\n") for setting in section.settings.values()]
    return "\n".join(configuration_lines) if configuration_lines else "（空节）"


def format_update_report(report: str) -> list[str]:
    conflict_match = re.match(r"^冲突，(.+?): \[([^]]+)\] (.*?) \| (新配置|旧配置): (.*)$", report)
    if conflict_match:
        action, section_name, first_value, second_label, second_value = conflict_match.groups()
        if second_label == "新配置":
            old_value, new_value = first_value, second_value
        else:
            old_value, new_value = second_value, first_value
        return [
            f"冲突：{action}  节：[{section_name}]",
            f"  旧配置：{old_value}",
            f"  新配置：{new_value}",
        ]
    return [report]


def print_update_plan(customer_path: Path, new_path: Path, reports: list[str]) -> bool:
    clear_current_screen()
    print()
    plan_lines = [
        f"客户旧配置：{customer_path}",
        f"新版配置：  {new_path}",
        "─" * 48,
        "本次选择与计划修改：",
    ]
    if reports:
        for report in reports:
            plan_lines.extend(format_update_report(report))
    else:
        plan_lines.append("没有需要修改的配置项。")
    print_panel("本次选择与计划修改", plan_lines)
    return prompt_choice("确认按以上选择执行更新？[Y/N]：", {"Y", "y", "N", "n"}).lower() == "y"



def print_final_result(final_lines: list[str]) -> None:
    if not final_lines:
        return

    if final_lines[0] == "配置更新完成。":
        try:
            details_index = final_lines.index("修改明细：")
        except ValueError:
            details_index = len(final_lines)
        summary_lines = final_lines[:details_index]
        detail_lines: list[str] = []
        for report in final_lines[details_index + 1 :]:
            detail_lines.extend(format_update_report(report))
        print_panel("配置更新完成", summary_lines)
        print()
        print_panel("修改明细", detail_lines or ["没有需要修改的配置项。"])
        return

    if final_lines[0] == "配置恢复完成。":
        print_panel("配置恢复完成", final_lines)
        return

    print_panel("执行结果", final_lines)


def complete_path(text: str, state: int) -> str | None:
    matches = sorted(glob.glob(f"{text}*"))
    candidates = [f"{match}/" if os.path.isdir(match) else match for match in matches]
    return candidates[state] if state < len(candidates) else None


def prompt_path(prompt: str) -> str:
    original_completer = readline.get_completer()
    original_delimiters = readline.get_completer_delims()
    try:
        readline.set_completer(complete_path)
        readline.set_completer_delims(" \t\n")
        readline.parse_and_bind("tab: complete")
        print_panel("文件路径输入", [prompt])
        path_value = input("  └─> ").strip()
        clear_current_screen()
        return path_value
    finally:
        readline.set_completer(original_completer)
        readline.set_completer_delims(original_delimiters)


def prompt_backup_name(prompt: str, backup_files: list[Path]) -> str:
    backup_names = sorted(backup_file.name for backup_file in backup_files)

    def complete_backup_name(text: str, state: int) -> str | None:
        matches = [backup_name for backup_name in backup_names if backup_name.startswith(text)]
        return matches[state] if state < len(matches) else None

    original_completer = readline.get_completer()
    original_delimiters = readline.get_completer_delims()
    try:
        readline.set_completer(complete_backup_name)
        readline.set_completer_delims(" \t\n")
        readline.parse_and_bind("tab: complete")
        print_panel("备份文件输入", [prompt])
        backup_name = input("  └─> ").strip()
        clear_current_screen()
        return backup_name
    finally:
        readline.set_completer(original_completer)
        readline.set_completer_delims(original_delimiters)


def choose_paths(customer_settings: Path, new_settings: Path) -> tuple[Path, Path]:
    while True:
        customer_input = prompt_path("请输入客户旧配置的绝对路径（可按 Tab 补全）：")
        new_input = prompt_path("请输入新版配置的绝对路径（可按 Tab 补全）：")
        customer_path = Path(customer_input)
        new_path = Path(new_input)

        if not customer_path.is_absolute() or not new_path.is_absolute():
            print("请输入两个绝对路径。")
        elif not customer_path.is_file() or not new_path.is_file():
            print("文件不存在或不是普通文件，请重新输入。")
        elif customer_path == new_path:
            print("新旧配置不能是同一个文件，请重新输入。")
        else:
            return customer_path, new_path
def summarize_differences(customer_document: IniDocument, new_document: IniDocument) -> dict[str, int]:
    summary = {"conflicts": 0, "new_settings": 0, "removed_settings": 0, "new_sections": 0, "removed_sections": 0}
    for section_name, customer_section in customer_document.sections.items():
        new_section = new_document.sections.get(section_name)
        if new_section is None:
            summary["removed_sections"] += 1
            continue
        for setting_name, customer_setting in customer_section.settings.items():
            new_setting = new_section.settings.get(setting_name)
            if new_setting is None:
                summary["removed_settings"] += 1
            elif customer_setting.line != new_setting.line:
                summary["conflicts"] += 1
        for setting_name in new_section.settings:
            if setting_name not in customer_section.settings:
                summary["new_settings"] += 1
    for section_name in new_document.sections:
        if section_name not in customer_document.sections:
            summary["new_sections"] += 1
    return summary


def choose_policy(customer_document: IniDocument, new_document: IniDocument) -> dict[str, str] | None:
    global customer_path, new_path
    while True:
        summary = summarize_differences(customer_document, new_document)
        print()
        print_panel("配置文件更新工具", [
            f"客户旧配置：{customer_path}",
            f"新版配置：  {new_path}",
            "─" * 48,
            "检测到的差异",
            f"  ● 值冲突：{summary['conflicts']} 项",
            f"  ● 新增配置：{summary['new_settings']} 项",
            f"  ● 新版删除的旧配置：{summary['removed_settings']} 项",
            f"  ● 新增节：{summary['new_sections']} 个",
            f"  ● 新版删除的旧节：{summary['removed_sections']} 个",
            "─" * 48,
            "【1】默认更新  【2】自定义更新  【3】恢复备份  【4】更换文件",
        ])
        selection = prompt_choice("请选择 [1-4]：", {"1", "2", "3", "4"})

        if selection == "1":
            return {
                "conflict": "first",
                "new_setting": "first",
                "removed_setting": "first",
                "new_section": "first",
                "removed_section": "first",
            }
        if selection == "2":
            policy = {
                "conflict": "first",
                "new_setting": "first",
                "removed_setting": "first",
                "new_section": "first",
                "removed_section": "first",
            }
            if summary["conflicts"]:
                policy["conflict"] = choose_category("值冲突", "保留旧值", "使用新值")
            if summary["new_settings"]:
                policy["new_setting"] = choose_category("新增配置", "添加", "不添加")
            if summary["removed_settings"]:
                policy["removed_setting"] = choose_category("新版删除的旧配置", "保留旧项", "删除旧项")
            if summary["new_sections"]:
                policy["new_section"] = choose_category("新增节", "添加整个节", "不添加")
            if summary["removed_sections"]:
                policy["removed_section"] = choose_category("新版删除的旧节", "保留旧节", "删除旧节")
            return policy
        if selection == "3":
            return None

        customer_path, new_path = choose_paths(customer_path, new_path)
        customer_document = read_document(customer_path)
        new_document = read_document(new_path)


def resolve_action(policy_value: str, title: str, details: list[str], first_action: str, second_action: str) -> str:
    if policy_value == "first":
        return "first"
    if policy_value == "second":
        return "second"
    return choose_per_item(title, details, first_action, second_action)


def merge_documents(customer_document: IniDocument, new_document: IniDocument, policy: dict[str, str]) -> tuple[str, list[str]]:
    reports: list[str] = []
    section_actions: dict[str, str] = {}
    setting_actions: dict[tuple[str, str], str] = {}

    for section_name, customer_section in customer_document.sections.items():
        new_section = new_document.sections.get(section_name)
        if new_section is None:
            action = resolve_action(
                policy["removed_section"],
                "新版删除节",
                [
                    f"节：[{section_name}]",
                    f"旧配置：{section_content(customer_section)}",
                    "新配置：（新版配置中不存在）",
                ],
                "保留旧节",
                "删除旧节",
            )
            section_actions[section_name] = "keep" if action == "first" else "delete"
            if action == "first":
                reports.append(f"保留旧节: [{section_name}]")
            else:
                reports.append(f"删除旧节: [{section_name}]")
            for customer_setting in customer_section.settings.values():
                reports.append(f"  配置: {customer_setting.line.rstrip()}")
            continue

        section_actions[section_name] = "keep"
        for setting_name, customer_setting in customer_section.settings.items():
            new_setting = new_section.settings.get(setting_name)
            if new_setting is None:
                action = resolve_action(
                    policy["removed_setting"],
                    "新版删除配置",
                    [f"节：[{section_name}]", f"旧配置：{customer_setting.line.rstrip()}"],
                    "保留旧项",
                    "删除旧项",
                )
                setting_actions[(section_name, setting_name)] = "keep_old" if action == "first" else "delete"
                reports.append(("保留旧配置" if action == "first" else "删除旧配置") + f": [{section_name}] {customer_setting.line.rstrip()}")
            elif customer_setting.line != new_setting.line:
                action = resolve_action(
                    policy["conflict"],
                    "值冲突",
                    [f"节：[{section_name}]", f"旧配置：{customer_setting.line.rstrip()}", f"新配置：{new_setting.line.rstrip()}"],
                    "保留旧值",
                    "使用新值",
                )
                setting_actions[(section_name, setting_name)] = "keep_old" if action == "first" else "use_new"
                if action == "first":
                    reports.append(f"冲突，保留旧值: [{section_name}] {customer_setting.line.rstrip()} | 新配置: {new_setting.line.rstrip()}")
                else:
                    reports.append(f"冲突，使用新值并移动到新版位置: [{section_name}] {new_setting.line.rstrip()} | 旧配置: {customer_setting.line.rstrip()}")

        for setting_name, new_setting in new_section.settings.items():
            if setting_name not in customer_section.settings:
                action = resolve_action(
                    policy["new_setting"],
                    "新增配置",
                    [f"节：[{section_name}]", f"新配置：{new_setting.line.rstrip()}"],
                    "添加", "不添加",
                )
                setting_actions[(section_name, setting_name)] = "add" if action == "first" else "not_add"
                reports.append(("新增配置" if action == "first" else "不添加新配置") + f": [{section_name}] {new_setting.line.rstrip()}")

    for section_name, new_section in new_document.sections.items():
        if section_name not in customer_document.sections:
            action = resolve_action(
                policy["new_section"],
                "新增节",
                [
                    f"节：[{section_name}]",
                    "旧配置：（旧配置中不存在）",
                    f"新配置：{section_content(new_section)}",
                ],
                "添加整个节", "不添加",
            )
            section_actions[section_name] = "add" if action == "first" else "not_add"
            reports.append(("新增节" if action == "first" else "不添加新节") + f": [{section_name}]")
            for new_setting in new_section.settings.values():
                reports.append(f"  配置: {new_setting.line.rstrip()}")

    output_lines = list(customer_document.prefix_lines)
    for section_name, customer_section in customer_document.sections.items():
        if section_actions.get(section_name) == "delete":
            continue
        new_section = new_document.sections.get(section_name)
        if new_section is None:
            output_lines.append(customer_section.header_line)
            output_lines.extend(customer_section.body_lines)
            continue
        output_lines.extend(render_existing_section(customer_section, new_section, setting_actions))

    for section_name, new_section in new_document.sections.items():
        if section_name not in customer_document.sections and section_actions.get(section_name) == "add":
            if output_lines and not output_lines[-1].strip():
                pass
            elif output_lines:
                output_lines.append("\n")
            output_lines.append(new_section.header_line)
            output_lines.extend(new_section.body_lines)

    return "".join(output_lines), reports


def render_existing_section(customer_section: Section, new_section: Section, setting_actions: dict[tuple[str, str], str]) -> list[str]:
    section_name = customer_section.name
    movable_settings = [
        setting_name
        for setting_name in new_section.settings
        if setting_actions.get((section_name, setting_name)) in {"add", "use_new"}
    ]
    retained_customer_settings = {
        setting_name
        for setting_name in customer_section.settings
        if setting_actions.get((section_name, setting_name)) not in {"delete", "use_new"}
    }
    insert_before: dict[str, list[str]] = {}
    append_at_end: list[str] = []
    new_setting_names = list(new_section.settings)

    for setting_name in movable_settings:
        setting_position = new_setting_names.index(setting_name)
        anchor_name = next(
            (
                following_name
                for following_name in new_setting_names[setting_position + 1 :]
                if following_name in retained_customer_settings
            ),
            None,
        )
        setting_line = new_section.settings[setting_name].line
        if anchor_name is None:
            append_at_end.append(setting_line)
        else:
            insert_before.setdefault(anchor_name, []).append(setting_line)

    rendered_body: list[str] = []
    for line in customer_section.body_lines:
        match = SETTING_PATTERN.match(line.rstrip("\r\n"))
        if match:
            setting_name = match.group(1).strip()
            rendered_body.extend(insert_before.pop(setting_name, []))
            action = setting_actions.get((section_name, setting_name))
            if action in {"delete", "use_new"}:
                continue
        rendered_body.append(line)

    for remaining_lines in insert_before.values():
        append_at_end.extend(remaining_lines)

    if append_at_end:
        insertion_index = len(rendered_body)
        while insertion_index > 0 and not rendered_body[insertion_index - 1].strip():
            insertion_index -= 1
        rendered_body[insertion_index:insertion_index] = append_at_end
        if insertion_index + len(append_at_end) == len(rendered_body) and rendered_body and rendered_body[-1].strip():
            rendered_body.append("\n")

    return [customer_section.header_line, *rendered_body]


def write_update(customer_path: Path, merged_content: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = customer_path.with_name(f"{customer_path.name}.bak.{timestamp}")
    subprocess.run(["sudo", "cp", "-p", str(customer_path), str(backup_path)], check=True)

    temporary_file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", errors="surrogateescape", newline="", delete=False)
    try:
        temporary_file.write(merged_content)
        temporary_file.close()
        with Path(temporary_file.name).open("rb") as input_file:
            subprocess.run(["sudo", "tee", str(customer_path)], stdin=input_file, stdout=subprocess.DEVNULL, check=True)
    finally:
        Path(temporary_file.name).unlink(missing_ok=True)
    return backup_path


def restore_backup_interactively(customer_path: Path) -> tuple[list[str], int]:
    backup_files = sorted(
        customer_path.parent.glob(f"{customer_path.name}.bak.*"),
        key=lambda backup_file: backup_file.stat().st_mtime,
        reverse=True,
    )
    if not backup_files:
        print_panel("恢复备份", [f"未找到 {customer_path.name} 的更新备份文件。"])
        return ["配置恢复未执行：没有可用的更新备份文件。"], 0

    print()
    print_panel(
        f"可恢复的 {customer_path.name} 备份文件（由新到旧）",
        [f"● {backup_file.name}" for backup_file in backup_files],
    )

    selected_backup: Path | None = None
    while selected_backup is None:
        backup_name = prompt_backup_name("请输入要恢复的备份文件名（可按 Tab 补全）：", backup_files)
        selected_backup = next((backup_file for backup_file in backup_files if backup_file.name == backup_name), None)
        if selected_backup is None:
            print("文件名不在上述备份列表中，请完整输入文件名。")

    confirmation = prompt_choice(f"确认恢复 {selected_backup.name} 吗？当前文件会先备份。[Y/N]：", {"Y", "y", "N", "n"})
    if confirmation.lower() != "y":
        print("已取消恢复。")
        return ["配置恢复已取消，未修改任何配置文件。"], 0

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    current_backup = customer_path.with_name(f"{customer_path.name}.bak.{timestamp}")
    try:
        subprocess.run(["sudo", "cp", "-p", str(customer_path), str(current_backup)], check=True)
        subprocess.run(["sudo", "cp", "-p", str(selected_backup), str(customer_path)], check=True)
    except subprocess.CalledProcessError as error:
        return [f"配置恢复失败：{error}"], 1

    return [
        "配置恢复完成。",
        f"恢复目标：{customer_path}",
        f"恢复来源：{selected_backup}",
        f"恢复前当前配置的备份：{current_backup}",
    ], 0


def main() -> int:
    global customer_path, new_path
    customer_path = DEFAULT_CUSTOMER_SETTINGS
    new_path = DEFAULT_NEW_SETTINGS
    final_lines: list[str] = []
    exit_status = 0

    clear_current_screen()
    try:
        if not customer_path.is_file() or not new_path.is_file():
            print("默认配置文件不存在，请选择其他新旧配置文件。")
            customer_path, new_path = choose_paths(customer_path, new_path)

        customer_document = read_document(customer_path)
        new_document = read_document(new_path)
        policy = choose_policy(customer_document, new_document)
        if policy is None:
            final_lines, exit_status = restore_backup_interactively(customer_path)
            return exit_status

        customer_document = read_document(customer_path)
        new_document = read_document(new_path)
        merged_content, reports = merge_documents(customer_document, new_document, policy)

        if not print_update_plan(customer_path, new_path, reports):
            final_lines = ["配置更新已取消，未修改任何配置文件。"]
            return 0

        try:
            backup_path = write_update(customer_path, merged_content)
        except subprocess.CalledProcessError as error:
            final_lines = [f"配置更新失败：{error}"]
            exit_status = 1
            return exit_status

        final_lines = [
            "配置更新完成。",
            f"客户旧配置：{customer_path}",
            f"新版配置：  {new_path}",
            f"更新前备份：{backup_path}",
            "修改明细：",
            *(reports or ["没有需要修改的配置项。"]),
        ]
        return 0
    except KeyboardInterrupt:
        # Ctrl+C 可能发生在 input() 的同一行，先换行以保证结果面板从新行开始绘制。
        sys.stdout.write("\r\n")
        sys.stdout.flush()
        final_lines = ["配置更新已取消，未修改任何配置文件。"]
        return 130
    finally:
        if final_lines:
            print_final_result(final_lines)


if __name__ == "__main__":
    sys.exit(main())
