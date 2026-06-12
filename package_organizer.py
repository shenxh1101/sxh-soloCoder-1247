#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安装包整理工具 - 自动整理下载文件夹中的安装包
"""

import argparse
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

PLATFORM_MAP = {
    ".exe": "windows",
    ".msi": "windows",
    ".dmg": "macos",
    ".pkg": "macos",
    ".deb": "linux",
    ".rpm": "linux",
    ".appimage": "linux",
    ".tar.gz": "linux",
    ".tar.bz2": "linux",
    ".tar.xz": "linux",
}

PLATFORM_DIRS = {
    "windows": "Windows",
    "macos": "macOS",
    "linux": "Linux",
    "unknown": "Unknown",
}

PLATFORM_LABELS = {
    "windows": "Windows",
    "macos": "macOS",
    "linux": "Linux",
    "unknown": "Unknown",
}

VERSION_PATTERNS = [
    re.compile(r"[-_](\d+\.\d+(?:\.\d+)?(?:\.\d+)?(?:[-_]?\w+)?)"),
    re.compile(r"[vV](\d+\.\d+(?:\.\d+)?(?:\.\d+)?)"),
    re.compile(r"(\d+\.\d+(?:\.\d+)?(?:\.\d+)?(?:[-_]?\w+)?)\.")
]

SETUP_PATTERN = re.compile(r"(setup|installer)", re.IGNORECASE)
NAME_CLEAN_PATTERN = re.compile(r"[-_](?:\d+[.-]|x86|x64|win|mac|linux)", re.IGNORECASE)
ARCH_PATTERN = re.compile(r"(?:[-_])(x86_64|x64|amd64|x86|i386|arm64|aarch64)", re.IGNORECASE)

CHANNEL_KEYWORDS = {
    "release_type": [
        ("stable", re.compile(r"[-_](stable)[-_]?", re.IGNORECASE)),
        ("beta", re.compile(r"[-_](beta)[-_]?", re.IGNORECASE)),
        ("alpha", re.compile(r"[-_](alpha)[-_]?", re.IGNORECASE)),
        ("dev", re.compile(r"[-_](dev|develop|development)[-_]?", re.IGNORECASE)),
        ("nightly", re.compile(r"[-_](nightly)[-_]?", re.IGNORECASE)),
        ("rc", re.compile(r"[-_](rc\d*)[-_]?", re.IGNORECASE)),
        ("preview", re.compile(r"[-_](preview)[-_]?", re.IGNORECASE)),
        ("snapshot", re.compile(r"[-_](snapshot)[-_]?", re.IGNORECASE)),
        ("release", re.compile(r"[-_](release|final)[-_]?", re.IGNORECASE)),
    ],
    "distribution": [
        ("portable", re.compile(r"[-_](portable)[-_]?", re.IGNORECASE)),
        ("offline", re.compile(r"[-_](offline)[-_]?", re.IGNORECASE)),
        ("online", re.compile(r"[-_](online)[-_]?", re.IGNORECASE)),
        ("web", re.compile(r"[-_](web)[-_]?", re.IGNORECASE)),
        ("lite", re.compile(r"[-_](lite|mini)[-_]?", re.IGNORECASE)),
        ("full", re.compile(r"[-_](full|complete)[-_]?", re.IGNORECASE)),
    ],
    "package_type": [
        ("universal", re.compile(r"[-_](universal|multi|any)[-_]?", re.IGNORECASE)),
        ("setup", re.compile(r"[-_](setup)[-_]?", re.IGNORECASE)),
        ("installer", re.compile(r"[-_](installer)[-_]?", re.IGNORECASE)),
        ("bundle", re.compile(r"[-_](bundle)[-_]?", re.IGNORECASE)),
    ],
}

DEFAULT_CONFIG_FILENAME = "package_organizer_config.json"
UNDO_RECORD_FILENAME = "package_organizer_undo.json"

TEMPLATE_VARS = {
    "name": "软件名",
    "version": "版本号",
    "platform": "平台(Windows/macOS/Linux)",
    "arch": "架构(x64/arm64等)",
    "release_type": "发行类型(stable/beta等)",
    "distribution": "发行渠道(portable/offline等)",
    "package_type": "打包类型(universal/setup等)",
}

DEFAULT_NAME_TEMPLATE = "{name}-{version}-{release_type}{distribution}{package_type}-{platform}-{arch}"



@dataclass
class PackageInfo:
    original_path: Path
    file_ext: str
    platform: str
    file_size: int = 0
    software_name: str = ""
    version: str = ""
    arch: str = ""
    release_type: str = ""
    distribution: str = ""
    package_type: str = ""
    new_filename: str = ""
    target_dir: Path = field(default_factory=Path)
    target_path: Path = field(default_factory=Path)
    sha256_hash: str = ""
    signature_status: str = "not_checked"
    signature_details: Optional[dict] = None
    skipped: bool = False
    skip_reason: str = ""
    moved: bool = False
    renamed: bool = False
    exclude_rule: str = ""


@dataclass
class ExcludedFileInfo:
    original_path: Path
    file_size: int = 0
    exclude_rule: str = ""
    exclude_detail: str = ""


class PackageOrganizer:
    def __init__(
        self,
        source_dir: Path,
        dry_run: bool = False,
        target_dir: Optional[Path] = None,
        exclude_patterns: Optional[list] = None,
        exclude_exts: Optional[list] = None,
        exclude_subdirs: Optional[list] = None,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        verify_signatures: bool = True,
        generate_manifest: bool = True,
        manifest_format: str = "json",
        append_manifest: bool = True,
        name_include_channel: bool = True,
        name_template: Optional[str] = None,
        custom_platform_dirs: Optional[dict] = None,
        profile_name: str = "default"
    ):
        self.source_dir = source_dir.expanduser().resolve()
        self.target_dir = (target_dir.expanduser().resolve() if target_dir else self.source_dir)
        self.dry_run = dry_run
        self.exclude_patterns = exclude_patterns or []
        self.exclude_exts = [e.lower() if e.startswith('.') else f'.{e.lower()}' for e in (exclude_exts or [])]
        self.exclude_subdirs = exclude_subdirs or []
        self.min_size = min_size
        self.max_size = max_size
        self.verify_signatures = verify_signatures
        self.generate_manifest = generate_manifest
        self.manifest_format = manifest_format
        self.append_manifest = append_manifest
        self.name_include_channel = name_include_channel
        self.name_template = name_template or DEFAULT_NAME_TEMPLATE
        self.custom_platform_dirs = custom_platform_dirs or {}
        self.profile_name = profile_name
        self.execution_time = self._get_current_timestamp()
        self.packages: list[PackageInfo] = []
        self.excluded_files: list[ExcludedFileInfo] = []
        self.undo_records: list[dict] = []
        self.stats = {
            "total": 0,
            "moved": 0,
            "renamed": 0,
            "skipped": 0,
            "excluded": 0,
            "signed": 0,
            "unsigned": 0,
            "sig_unknown": 0,
        }

    def run(self):
        logger.info(f"{'='*60}")
        logger.info(f"安装包整理工具")
        logger.info(f"{'='*60}")
        logger.info(f"源目录: {self.source_dir}")
        if self.target_dir != self.source_dir:
            logger.info(f"目标目录: {self.target_dir}")
        if self.profile_name and self.profile_name != "default":
            logger.info(f"使用配置档案: {self.profile_name}")
        logger.info(f"执行时间: {self.execution_time}")
        logger.info(f"试运行模式: {'开启' if self.dry_run else '关闭'}")
        logger.info(f"签名验证: {'开启' if self.verify_signatures else '关闭'}")
        logger.info(f"清单追加模式: {'开启' if self.append_manifest else '关闭'}")
        logger.info(f"文件名模板: {self.name_template}")
        logger.info(f"文件名包含渠道信息: {'开启' if self.name_include_channel else '关闭'}")
        if self.custom_platform_dirs:
            for p, d in self.custom_platform_dirs.items():
                logger.info(f"自定义目录映射: {p} -> {d}")
        if self.exclude_patterns:
            logger.info(f"排除文件名匹配: {', '.join(self.exclude_patterns)}")
        if self.exclude_exts:
            logger.info(f"排除扩展名: {', '.join(self.exclude_exts)}")
        if self.exclude_subdirs:
            logger.info(f"排除子目录: {', '.join(self.exclude_subdirs)}")
        if self.min_size is not None:
            logger.info(f"最小文件大小: {self._format_size(self.min_size)}")
        if self.max_size is not None:
            logger.info(f"最大文件大小: {self._format_size(self.max_size)}")
        logger.info("")

        if not self.source_dir.exists():
            logger.error(f"错误: 源目录不存在: {self.source_dir}")
            sys.exit(1)

        self._scan_files()
        self._process_packages()

        if self.dry_run:
            self._print_preview_report()
        else:
            self._print_summary()
            if self.undo_records:
                self._save_undo_records()

        if self.generate_manifest:
            self._generate_manifest()

        logger.info("")
        logger.info("完成!")

    def _scan_files(self):
        logger.info("扫描文件中...")
        for filepath in self.source_dir.rglob("*"):
            if not filepath.is_file():
                continue

            if filepath.name.startswith("package_manifest.") or filepath.name.startswith("package_organizer_config."):
                continue

            try:
                file_size = filepath.stat().st_size
            except OSError:
                file_size = 0

            exclude_result = self._check_exclusion(filepath, file_size)
            if exclude_result:
                rule, detail = exclude_result
                self.excluded_files.append(ExcludedFileInfo(
                    original_path=filepath,
                    file_size=file_size,
                    exclude_rule=rule,
                    exclude_detail=detail
                ))
                self.stats["excluded"] += 1
                continue

            file_ext = self._get_package_extension(filepath.name)
            if not file_ext:
                continue

            platform = PLATFORM_MAP.get(file_ext.lower(), "unknown")
            pkg = PackageInfo(
                original_path=filepath,
                file_ext=file_ext,
                platform=platform,
                file_size=file_size
            )
            self.packages.append(pkg)
            self.stats["total"] += 1

        logger.info(f"找到 {len(self.packages)} 个安装包文件，排除 {len(self.excluded_files)} 个文件")
        logger.info("")

    def _check_exclusion(self, filepath: Path, file_size: int) -> Optional[tuple]:
        filename = filepath.name

        if self._is_in_excluded_subdir(filepath):
            try:
                rel = str(filepath.relative_to(self.source_dir).parent)
                return ("子目录排除", f"命中排除目录: {rel}")
            except ValueError:
                return ("子目录排除", "")

        file_ext_lower = filepath.suffix.lower()
        if self.exclude_exts and file_ext_lower in self.exclude_exts:
            return ("扩展名排除", f"扩展名: {file_ext_lower}")

        for pattern in self.exclude_patterns:
            if re.search(pattern, filename, re.IGNORECASE):
                return ("文件名排除", f"匹配模式: {pattern}")

        if not self._check_size_limit(file_size):
            size_str = self._format_size(file_size)
            reason = []
            if self.min_size is not None and file_size < self.min_size:
                reason.append(f"< {self._format_size(self.min_size)}")
            if self.max_size is not None and file_size > self.max_size:
                reason.append(f"> {self._format_size(self.max_size)}")
            return ("文件大小排除", f"{size_str} {' '.join(reason)}")

        return None

    def _is_in_excluded_subdir(self, filepath: Path) -> bool:
        if not self.exclude_subdirs:
            return False
        try:
            relative_path = filepath.relative_to(self.source_dir)
            path_parts = set(relative_path.parts[:-1])
            for subdir in self.exclude_subdirs:
                subdir_clean = Path(subdir).name
                if subdir_clean in path_parts:
                    return True
        except ValueError:
            pass
        return False

    def _check_size_limit(self, file_size: int) -> bool:
        if self.min_size is not None and file_size < self.min_size:
            return False
        if self.max_size is not None and file_size > self.max_size:
            return False
        return True

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _get_package_extension(self, filename: str) -> Optional[str]:
        name_lower = filename.lower()
        for ext in sorted(PLATFORM_MAP.keys(), key=len, reverse=True):
            if name_lower.endswith(ext):
                return filename[-len(ext):]
        return None

    def _process_packages(self):
        for pkg in self.packages:
            self._extract_package_info(pkg)
            self._calculate_hash(pkg)

            if self.verify_signatures:
                self._verify_signature(pkg)

            self._prepare_target_path(pkg)
            self._move_file(pkg)

            logger.info("")

    def _extract_package_info(self, pkg: PackageInfo):
        filename = pkg.original_path.stem
        file_ext = pkg.file_ext

        logger.info(f"处理: {pkg.original_path.name}")

        for label in PLATFORM_LABELS.values():
            filename = re.sub(rf"[-_]{label}[-_]?", "-", filename, flags=re.IGNORECASE)

        for category, keywords in CHANNEL_KEYWORDS.items():
            for value, pattern in keywords:
                match = pattern.search(filename)
                if match:
                    setattr(pkg, category, value)
                    filename = filename[:match.start()] + "-" + filename[match.end():]

        arch_match = ARCH_PATTERN.search(filename)
        if arch_match:
            pkg.arch = arch_match.group(1).lower()
            filename = filename[:arch_match.start()] + filename[arch_match.end():]

        version = ""
        for pattern in VERSION_PATTERNS:
            match = pattern.search(filename)
            if match:
                version = match.group(1)
                filename = filename[:match.start()] + filename[match.end():]
                break
        pkg.version = version

        name = filename.strip(" -_.")
        name = NAME_CLEAN_PATTERN.sub("", name)
        name = SETUP_PATTERN.sub("", name)
        name = re.sub(r"[-_]+", "-", name)
        name = name.strip(" -")
        pkg.software_name = name

        logger.debug(f"  软件名: {pkg.software_name}")
        logger.debug(f"  版本: {pkg.version or '未识别'}")
        logger.debug(f"  架构: {pkg.arch or '未识别'}")
        logger.debug(f"  平台: {pkg.platform}")
        if pkg.release_type:
            logger.debug(f"  发行类型: {pkg.release_type}")
        if pkg.distribution:
            logger.debug(f"  发行渠道: {pkg.distribution}")
        if pkg.package_type:
            logger.debug(f"  打包类型: {pkg.package_type}")

    def _calculate_hash(self, pkg: PackageInfo):
        logger.debug(f"  计算 SHA256 哈希...")
        sha256 = hashlib.sha256()
        with open(pkg.original_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        pkg.sha256_hash = sha256.hexdigest()
        logger.debug(f"  SHA256: {pkg.sha256_hash[:16]}...")

    def _verify_signature(self, pkg: PackageInfo):
        logger.debug(f"  验证数字签名...")
        sig_result = self._check_signature(pkg.original_path, pkg.platform)
        pkg.signature_status = sig_result["status"]
        pkg.signature_details = sig_result.get("details")

        if sig_result["status"] == "signed":
            self.stats["signed"] += 1
            signer = sig_result.get("details", {}).get("signer", "未知")
            logger.info(f"  ✓ 已签名: {signer}")
        elif sig_result["status"] == "unsigned":
            self.stats["unsigned"] += 1
            logger.warning(f"  ✗ 未签名 - 请注意安全风险")
        else:
            self.stats["sig_unknown"] += 1
            logger.info(f"  ? 无法验证: {sig_result.get('message', '未知原因')}")

    def _check_signature(self, filepath: Path, platform: str) -> dict:
        if platform == "windows" and sys.platform == "win32":
            return self._check_windows_signature(filepath)
        elif platform == "macos" and sys.platform == "darwin":
            return self._check_macos_signature(filepath)
        else:
            return {
                "status": "unknown",
                "message": f"当前系统不支持 {platform} 平台的签名验证"
            }

    def _check_windows_signature(self, filepath: Path) -> dict:
        try:
            import subprocess
            file_path_str = str(filepath).replace("'", "''")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-OutputEncoding", "UTF8",
                 "-Command",
                 "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                 f"Get-AuthenticodeSignature -FilePath '{file_path_str}' | "
                 "Select-Object -Property Status, StatusMessage, SignerCertificate | "
                 "ConvertTo-Json -Compress"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            stdout = result.stdout or ""
            if result.returncode == 0 and stdout.strip():
                try:
                    data = json.loads(stdout)
                except json.JSONDecodeError:
                    return {"status": "unknown", "message": "无法解析签名信息"}
                status = data.get("Status", "")
                status_map = {
                    "Valid": "signed",
                    "NotSigned": "unsigned",
                    0: "signed",
                    2: "unsigned",
                    "0": "signed",
                    "2": "unsigned",
                }
                mapped_status = status_map.get(status, "unknown")
                if mapped_status == "signed":
                    signer = data.get("SignerCertificate", {}) or {}
                    subject = signer.get("Subject", "")
                    cn_match = re.search(r"CN=([^,]+)", subject)
                    signer_name = cn_match.group(1) if cn_match else subject
                    return {
                        "status": "signed",
                        "details": {
                            "signer": signer_name,
                            "thumbprint": signer.get("Thumbprint", ""),
                            "valid_from": signer.get("NotBefore", ""),
                            "valid_to": signer.get("NotAfter", "")
                        }
                    }
                elif mapped_status == "unsigned":
                    return {"status": "unsigned"}
                else:
                    msg = data.get("StatusMessage", "")
                    if not msg or any(ord(c) > 127 and ord(c) < 256 for c in msg):
                        msg = f"签名状态: {status}"
                    return {"status": "unknown", "message": msg}
        except Exception as e:
            logger.debug(f"  签名验证出错: {e}")
        return {"status": "unknown", "message": "PowerShell 调用失败"}

    def _check_macos_signature(self, filepath: Path) -> dict:
        try:
            import subprocess
            result = subprocess.run(
                ["codesign", "-dv", str(filepath)],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                output = result.stderr or result.stdout
                identifier = ""
                authority = ""
                for line in output.splitlines():
                    if "Identifier=" in line:
                        identifier = line.split("=", 1)[1]
                    elif "Authority=" in line:
                        authority = line.split("=", 1)[1]
                return {
                    "status": "signed",
                    "details": {
                        "signer": authority or identifier,
                        "identifier": identifier
                    }
                }
            else:
                result2 = subprocess.run(
                    ["codesign", "--verify", str(filepath)],
                    capture_output=True,
                    text=True
                )
                if "code object is not signed" in (result2.stderr or result2.stdout):
                    return {"status": "unsigned"}
                return {"status": "unknown", "message": "codesign 验证失败"}
        except Exception as e:
            logger.debug(f"  签名验证出错: {e}")
        return {"status": "unknown", "message": "codesign 调用失败"}

    def _get_platform_dir(self, platform: str) -> Path:
        if platform in self.custom_platform_dirs:
            custom_dir = self.custom_platform_dirs[platform]
            if os.path.isabs(custom_dir):
                return Path(custom_dir)
            else:
                return self.target_dir / custom_dir
        default_dir_name = PLATFORM_DIRS.get(platform, "Unknown")
        return self.target_dir / default_dir_name

    def _apply_name_template(self, pkg: PackageInfo) -> str:
        platform_label = PLATFORM_LABELS.get(pkg.platform, "Unknown")

        template_values = {
            "name": pkg.software_name or pkg.original_path.stem,
            "version": pkg.version,
            "platform": platform_label,
            "arch": pkg.arch,
            "release_type": pkg.release_type if (self.name_include_channel and pkg.release_type != "stable") else "",
            "distribution": pkg.distribution if self.name_include_channel else "",
            "package_type": pkg.package_type if (self.name_include_channel and pkg.package_type not in ["setup", "installer"]) else "",
        }

        result = self.name_template

        for key in ["release_type", "distribution", "package_type"]:
            val = template_values.get(key, "")
            if val:
                if key != "name":
                    placeholder = "{" + key + "}"
                    result = result.replace(placeholder, "-" + val)
                else:
                    placeholder = "{" + key + "}"
                    result = result.replace(placeholder, val)
            else:
                placeholder = "{" + key + "}"
                result = result.replace(placeholder, "")

        for key in ["name", "version", "platform", "arch"]:
            val = template_values.get(key, "")
            placeholder = "{" + key + "}"
            if val:
                result = result.replace(placeholder, val)
            else:
                result = result.replace(placeholder, "")

        for label in PLATFORM_LABELS.values():
            result = re.sub(rf"[-_]{label}[-_]?", "-", result, flags=re.IGNORECASE)

        result = re.sub(r"-{2,}", "-", result)
        result = result.strip(" -")

        if not result:
            result = pkg.original_path.stem

        return f"{result}{pkg.file_ext}"

    def _prepare_target_path(self, pkg: PackageInfo):
        pkg.target_dir = self._get_platform_dir(pkg.platform)
        pkg.new_filename = self._apply_name_template(pkg)
        pkg.target_path = pkg.target_dir / pkg.new_filename

        counter = 1
        while pkg.target_path.exists() and pkg.target_path.resolve() != pkg.original_path.resolve():
            stem = pkg.new_filename[:-len(pkg.file_ext)] if pkg.new_filename.endswith(pkg.file_ext) else pkg.new_filename
            pkg.new_filename = f"{stem}-{counter}{pkg.file_ext}"
            pkg.target_path = pkg.target_dir / pkg.new_filename
            counter += 1

        logger.debug(f"  目标路径: {pkg.target_dir.name}/{pkg.target_path.name}")

    def _move_file(self, pkg: PackageInfo):
        if pkg.target_path.resolve() == pkg.original_path.resolve():
            logger.info(f"  - 跳过: 文件已在正确位置")
            pkg.skipped = True
            pkg.skip_reason = "已在正确位置"
            self.stats["skipped"] += 1
            return

        if pkg.original_path.name != pkg.target_path.name:
            pkg.renamed = True
            self.stats["renamed"] += 1

        if self.dry_run:
            action = "将移动并重命名" if pkg.renamed else "将移动"
            logger.info(f"  [试运行] {action}: {pkg.original_path.name} -> "
                       f"{pkg.target_dir.name}/{pkg.target_path.name}")
            return

        try:
            pkg.target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pkg.original_path), str(pkg.target_path))
            pkg.moved = True
            self.stats["moved"] += 1
            action = "已移动并重命名" if pkg.renamed else "已移动"
            logger.info(f"  ✓ {action}: {pkg.target_dir.name}/{pkg.target_path.name}")

            self.undo_records.append({
                "from": str(pkg.target_path.resolve()),
                "to": str(pkg.original_path.resolve()),
                "original_name": pkg.original_path.name,
                "new_name": pkg.target_path.name,
                "size": pkg.file_size,
                "sha256": pkg.sha256_hash,
            })
        except Exception as e:
            pkg.skipped = True
            pkg.skip_reason = f"移动失败: {e}"
            self.stats["skipped"] += 1
            logger.error(f"  ✗ 移动失败: {e}")

    def _print_preview_report(self):
        logger.info("")
        logger.info(f"{'='*60}")
        logger.info("试运行预览报告")
        logger.info(f"{'='*60}")

        will_move = [p for p in self.packages if not p.skipped]
        will_rename = [p for p in will_move if p.renamed]
        will_skip = [p for p in self.packages if p.skipped]
        sig_risk = [p for p in self.packages if p.signature_status == "unsigned"]

        if will_move:
            logger.info("")
            logger.info(f"【将移动】({len(will_move)} 个文件)")
            logger.info(f"{'-'*60}")
            for pkg in will_move:
                rename_tag = " [重命名]" if pkg.renamed else ""
                size_str = self._format_size(pkg.file_size)
                channel_tags = []
                if pkg.release_type:
                    channel_tags.append(pkg.release_type)
                if pkg.distribution:
                    channel_tags.append(pkg.distribution)
                if pkg.package_type:
                    channel_tags.append(pkg.package_type)
                channel_str = f" [{', '.join(channel_tags)}]" if channel_tags else ""
                logger.info(f"  {pkg.original_path.name} ({size_str}){rename_tag}{channel_str}")
                logger.info(f"    -> {pkg.target_dir.name}/{pkg.target_path.name}")

        if will_rename:
            logger.info("")
            logger.info(f"【将重命名】({len(will_rename)} 个文件)")
            logger.info(f"{'-'*60}")
            for pkg in will_rename:
                logger.info(f"  {pkg.original_path.name}")
                logger.info(f"    -> {pkg.target_path.name}")

        if will_skip:
            logger.info("")
            logger.info(f"【将跳过】({len(will_skip)} 个文件)")
            logger.info(f"{'-'*60}")
            for pkg in will_skip:
                logger.info(f"  {pkg.original_path.name} - {pkg.skip_reason}")

        if self.excluded_files:
            exclude_groups: dict[str, list] = {}
            for ef in self.excluded_files:
                exclude_groups.setdefault(ef.exclude_rule, []).append(ef)

            logger.info("")
            logger.info(f"【被排除】({len(self.excluded_files)} 个文件)")
            logger.info(f"{'-'*60}")
            for rule, files in exclude_groups.items():
                logger.info(f"  ▸ {rule} ({len(files)} 个文件:")
                for ef in files[:5]:
                    try:
                        rel = str(ef.original_path.relative_to(self.source_dir))
                    except ValueError:
                        rel = ef.original_path.name
                    size_str = self._format_size(ef.file_size)
                    logger.info(f"    - {rel} ({size_str}) [{ef.exclude_detail}]")
                if len(files) > 5:
                    logger.info(f"    ... 还有 {len(files) - 5} 个文件")

        if sig_risk:
            logger.info("")
            logger.warning(f"【签名风险】({len(sig_risk)} 个文件未签名)")
            logger.warning(f"{'-'*60}")
            for pkg in sig_risk:
                size_str = self._format_size(pkg.file_size)
                logger.warning(f"  ⚠  {pkg.original_path.name} ({size_str}) - 未签名")

        logger.info("")
        logger.info(f"{'='*60}")
        logger.info("预览汇总")
        logger.info(f"{'='*60}")
        logger.info(f"扫描文件总数: {self.stats['total'] + self.stats['excluded']}")
        logger.info(f"  识别安装包: {self.stats['total']}")
        logger.info(f"  排除文件数: {self.stats['excluded']}")
        logger.info(f"将移动:      {len(will_move)}")
        logger.info(f"  其中重命名:{len(will_rename)}")
        logger.info(f"将跳过:      {len(will_skip)}")
        if self.verify_signatures:
            logger.info(f"已签名:      {self.stats['signed']}")
            logger.warning(f"未签名:      {self.stats['unsigned']} ⚠")
            logger.info(f"无法验证:    {self.stats['sig_unknown']}")

        if sig_risk:
            logger.warning("")
            logger.warning("提示: 发现未签名的安装包，请谨慎操作！")

    def _print_summary(self):
        logger.info("")
        logger.info(f"{'='*60}")
        logger.info("处理摘要")
        logger.info(f"{'='*60}")
        logger.info(f"扫描文件总数: {self.stats['total'] + self.stats['excluded']}")
        logger.info(f"  识别安装包: {self.stats['total']}")
        logger.info(f"  排除文件数: {self.stats['excluded']}")
        logger.info(f"已移动:      {self.stats['moved']}")
        logger.info(f"  其中重命名:{self.stats['renamed']}")
        logger.info(f"跳过:        {self.stats['skipped']}")
        if self.verify_signatures:
            logger.info(f"已签名:      {self.stats['signed']}")
            logger.warning(f"未签名:      {self.stats['unsigned']}")
            logger.info(f"无法验证:    {self.stats['sig_unknown']}")
        if self.undo_records:
            logger.info(f"可撤销文件:  {len(self.undo_records)} (使用 --undo 撤销)")

    def _save_undo_records(self):
        undo_path = self.source_dir / UNDO_RECORD_FILENAME
        try:
            existing = []
            if undo_path.exists():
                try:
                    with open(undo_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        existing = data.get("undo_history", [])
                except (json.JSONDecodeError, IOError):
                    pass

            undo_entry = {
                "timestamp": self.execution_time,
                "profile": self.profile_name,
                "dry_run": self.dry_run,
                "source_directory": str(self.source_dir),
                "target_directory": str(self.target_dir),
                "moved_count": len(self.undo_records),
                "renamed_count": sum(1 for r in self.undo_records if r.get("original_name") != r.get("new_name")),
                "records": self.undo_records
            }
            existing.insert(0, undo_entry)

            with open(undo_path, "w", encoding="utf-8") as f:
                json.dump({
                    "undo_version": "2.0",
                    "last_updated": self._get_current_timestamp(),
                    "undo_history": existing[:20]
                }, f, ensure_ascii=False, indent=2)
            logger.info(f"✓ 撤销记录已保存: {UNDO_RECORD_FILENAME} (共 {len(self.undo_records)} 个文件可撤销)")
        except Exception as e:
            logger.error(f"保存撤销记录失败: {e}")

    @staticmethod
    def list_undo_history(source_dir: Path, limit: int = 10) -> bool:
        undo_path = source_dir / UNDO_RECORD_FILENAME
        if not undo_path.exists():
            logger.error(f"找不到撤销记录文件: {undo_path}")
            return False

        try:
            with open(undo_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            history = data.get("undo_history", [])
            if not history:
                logger.warning("没有整理记录")
                return True

            history = history[:limit]

            logger.info(f"{'='*60}")
            logger.info(f"最近整理记录 (共 {len(history)} 条，--undo-list 查看)")
            logger.info(f"{'='*60}")

            headers = [
                ("#", 3), ("时间", 19), ("档案", 10), ("模式", 6),
                ("移动数", 6), ("重命名数", 8), ("目标目录", 40)
            ]
            header_line = " | ".join(h.ljust(w) for h, w in headers)
            sep_line = "-+-".join("-" * w for _, w in headers)
            logger.info(header_line)
            logger.info(sep_line)

            for i, entry in enumerate(history):
                target_dir = entry.get("target_directory", "")
                if len(target_dir) > 38:
                    target_dir = "…" + target_dir[-37:]
                mode = "试运行" if entry.get("dry_run") else "实执行"
                row = [
                    str(i).ljust(3),
                    str(entry.get("timestamp", ""))[-19:].ljust(19),
                    (entry.get("profile", "default") or "default")[:9].ljust(10),
                    mode.ljust(6),
                    str(entry.get("moved_count", 0)).ljust(6),
                    str(entry.get("renamed_count", 0)).ljust(8),
                    target_dir.ljust(40)
                ]
                logger.info(" | ".join(row))

            logger.info("")
            logger.info("使用方法:")
            logger.info("  --undo --undo-index N           撤销第 N 批 (默认 0)")
            logger.info("  --undo --undo-index N --undo-files 1,3,5  只撤销第 N 批里的第 1/3/5 个文件")
            logger.info("  撤销前默认试运行，加 --execute-undo 真正执行")
            return True

        except Exception as e:
            logger.error(f"读取撤销历史失败: {e}")
            return False

    @staticmethod
    def undo_last_operation(
        source_dir: Path,
        dry_run: bool = True,
        index: int = 0,
        file_indices: Optional[list[int]] = None
    ) -> bool:
        undo_path = source_dir / UNDO_RECORD_FILENAME
        if not undo_path.exists():
            logger.error(f"找不到撤销记录文件: {undo_path}")
            return False

        try:
            with open(undo_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            history = data.get("undo_history", [])
            if not history:
                logger.error("没有可撤销的操作记录")
                return False

            if index >= len(history):
                logger.error(f"撤销记录索引超出范围: {index}，最多 {len(history) - 1}")
                return False

            entry = history[index]
            all_records = entry.get("records", [])

            if file_indices:
                selected = [r for i, r in enumerate(all_records) if i in file_indices]
                if not selected:
                    logger.error(f"没有选中要撤销的文件，可用索引: 0~{len(all_records) - 1}")
                    return False
                records = selected
                logger.info(f"已选择撤销 {len(records)}/{len(all_records)} 个文件")
            else:
                records = all_records

            logger.info(f"{'='*60}")
            logger.info(f"撤销操作 - {entry.get('timestamp', '未知时间')} (批次 #{index})")
            logger.info(f"{'='*60}")
            logger.info(f"档案: {entry.get('profile', 'default')}")
            logger.info(f"模式: {'试运行' if entry.get('dry_run') else '实际执行'}")
            logger.info(f"源目录: {entry.get('source_directory', '')}")
            logger.info(f"目标目录: {entry.get('target_directory', '')}")
            logger.info(f"待撤销文件数: {len(records)}/{entry.get('moved_count', len(all_records))}")
            logger.info("")

            success_count = 0
            fail_count = 0

            for i, record in enumerate(records, 1):
                from_path = Path(record["from"])
                to_path = Path(record["to"])
                size_str = PackageOrganizer._static_format_size(record.get("size", 0))

                if dry_run:
                    logger.info(f"  [{i}/{len(records)}] [试运行] 将撤销: "
                               f"{from_path.name} ({size_str}) -> {to_path.parent.name}/{to_path.name}")
                    success_count += 1
                    continue

                if not from_path.exists():
                    logger.warning(f"  [{i}/{len(records)}] ⚠  源文件不存在，已跳过: {from_path}")
                    fail_count += 1
                    continue

                if to_path.exists():
                    logger.warning(f"  [{i}/{len(records)}] ⚠  目标路径已存在，已跳过: {to_path}")
                    fail_count += 1
                    continue

                try:
                    to_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(from_path), str(to_path))
                    logger.info(f"  [{i}/{len(records)}] ✓ 已撤销: "
                               f"{from_path.name} ({size_str}) -> {to_path.parent.name}/{to_path.name}")
                    success_count += 1
                except Exception as e:
                    logger.error(f"  [{i}/{len(records)}] ✗ 撤销失败: {e}")
                    fail_count += 1

            logger.info("")
            logger.info(f"撤销完成: 成功 {success_count}，失败 {fail_count}")
            if not dry_run:
                logger.info("提示: 如需彻底移除撤销记录，可手动删除 package_organizer_undo.json")

            return True

        except Exception as e:
            logger.error(f"撤销操作失败: {e}")
            return False

    @staticmethod
    def _static_format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    @staticmethod
    def _load_manifest_records(source_dir: Path, manifest_format: str = "json") -> list:
        manifest_path = source_dir / f"package_manifest.{manifest_format}"
        if not manifest_path.exists():
            return []

        records = []
        try:
            if manifest_format == "json":
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                history = data.get("execution_history", [])
                for exec_idx, exec_record in enumerate(history):
                    exec_time = exec_record.get("execution_time", "")
                    is_dry = exec_record.get("dry_run", False)
                    profile = exec_record.get("profile", "default")
                    target_dir = exec_record.get("target_directory", "")
                    for pkg in exec_record.get("packages", []):
                        sig = pkg.get("signature", {})
                        sig_status = sig.get("status", "") if isinstance(sig, dict) else ""
                        sig_details = sig.get("details", {}) if isinstance(sig, dict) else {}
                        signer = sig_details.get("signer", "") if isinstance(sig_details, dict) else ""

                        records.append({
                            "exec_index": exec_idx,
                            "execution_time": exec_time,
                            "dry_run": is_dry,
                            "profile": profile,
                            "target_directory": target_dir,
                            "original_filename": pkg.get("original_filename", ""),
                            "new_filename": pkg.get("new_filename", ""),
                            "software_name": pkg.get("software_name", ""),
                            "version": pkg.get("version", ""),
                            "platform": pkg.get("platform", ""),
                            "release_type": pkg.get("release_type", ""),
                            "distribution": pkg.get("distribution", ""),
                            "package_type": pkg.get("package_type", ""),
                            "architecture": pkg.get("architecture", ""),
                            "file_size": pkg.get("file_size", 0),
                            "file_size_human": pkg.get("file_size_human", ""),
                            "signature_status": sig_status,
                            "signer": signer,
                            "moved": pkg.get("moved", False),
                            "renamed": pkg.get("renamed", False),
                            "sha256_hash": pkg.get("sha256_hash", "")
                        })
            elif manifest_format == "csv":
                import csv
                mode_map = {"试运行": True, "实际执行": False}
                sig_map = {"已签名": "signed", "未签名": "unsigned", "无法验证": "unknown", "未检查": "not_checked"}
                with open(manifest_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for exec_idx, row in enumerate(reader):
                        original = row.get("原文件名", "")
                        if original == "" or row.get("新文件名", "") == "(已排除)":
                            continue
                        dry_run_val = mode_map.get(row.get("执行模式", ""), False)
                        records.append({
                            "exec_index": exec_idx,
                            "execution_time": row.get("执行时间", ""),
                            "dry_run": dry_run_val,
                            "profile": "default",
                            "target_directory": "",
                            "original_filename": original,
                            "new_filename": row.get("新文件名", ""),
                            "software_name": row.get("软件名", ""),
                            "version": row.get("版本", ""),
                            "platform": row.get("平台", ""),
                            "release_type": row.get("发行类型", ""),
                            "distribution": row.get("发行渠道", ""),
                            "package_type": row.get("打包类型", ""),
                            "architecture": row.get("架构", ""),
                            "file_size": 0,
                            "file_size_human": row.get("文件大小", ""),
                            "signature_status": sig_map.get(row.get("签名状态", ""), row.get("签名状态", "")),
                            "signer": row.get("签名者", ""),
                            "moved": row.get("是否移动", "") == "是",
                            "renamed": row.get("是否重命名", "") == "是",
                            "sha256_hash": row.get("SHA256", "")
                        })
        except Exception as e:
            logger.warning(f"读取清单记录失败: {e}")
        return records

    @staticmethod
    def _filter_manifest_records(
        records: list,
        platform: Optional[str] = None,
        release_type: Optional[str] = None,
        distribution: Optional[str] = None,
        dry_run_only: Optional[bool] = None,
        signature_status: Optional[str] = None,
    ) -> list:
        results = []
        for r in records:
            if dry_run_only is not None and r["dry_run"] != dry_run_only:
                continue
            if platform and platform.lower() not in r["platform"].lower():
                continue
            if release_type and release_type.lower() not in r["release_type"].lower():
                continue
            if distribution and distribution.lower() not in r["distribution"].lower():
                continue
            if signature_status and signature_status.lower() != r["signature_status"].lower():
                continue
            results.append(r)
        return results

    @staticmethod
    def analyze_manifest_history(
        source_dir: Path,
        manifest_format: str = "json",
        platform: Optional[str] = None,
        release_type: Optional[str] = None,
        distribution: Optional[str] = None,
        dry_run_only: Optional[bool] = None,
        signature_status: Optional[str] = None,
        output_format: str = "table",
        group_by: str = "software_name"
    ) -> bool:
        records = PackageOrganizer._load_manifest_records(source_dir, manifest_format)
        if not records:
            logger.warning("清单中没有历史记录或找不到清单文件")
            return False

        records = PackageOrganizer._filter_manifest_records(
            records, platform, release_type, distribution, dry_run_only, signature_status
        )
        if not records:
            logger.warning("没有符合筛选条件的记录")
            return True

        logger.info(f"{'='*60}")
        logger.info(f"历史分析汇总视图")
        logger.info(f"{'='*60}")
        logger.info(f"筛选后记录数: {len(records)}")
        logger.info(f"分组依据: {group_by}")
        logger.info("")

        valid_groups = ["software_name", "platform", "distribution", "release_type", "signature_status"]
        if group_by not in valid_groups:
            group_by = "software_name"

        grouped: dict[str, list] = {}
        for r in records:
            key = r.get(group_by, "(未知)") or "(未知)"
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(r)

        summary = []
        for key, items in sorted(grouped.items()):
            platforms = sorted({i["platform"] for i in items if i.get("platform")})
            channels = sorted({i["distribution"] for i in items if i.get("distribution")})
            signatures = sorted({i["signature_status"] for i in items if i.get("signature_status")})
            versions = sorted({i["version"] for i in items if i.get("version")})

            summary.append({
                "group": key,
                "count": len(items),
                "unique_versions": len(versions),
                "platforms": ",".join(platforms) if platforms else "-",
                "channels": ",".join(channels) if channels else "-",
                "signatures": ",".join(signatures) if signatures else "-",
                "versions": ",".join(versions[:5]) if versions else "-",
                "latest_time": max(i["execution_time"] for i in items),
            })

        if output_format == "json":
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return True
        elif output_format == "csv":
            import csv
            import io
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=summary[0].keys())
            writer.writeheader()
            writer.writerows(summary)
            print(output.getvalue())
            return True

        headers = [
            (group_by, 20), ("数量", 6), ("版本数", 6), ("平台", 18),
            ("渠道", 16), ("签名状态", 12), ("最近整理时间", 19)
        ]
        header_line = " | ".join(h.ljust(w) for h, w in headers)
        sep_line = "-+-".join("-" * w for _, w in headers)
        logger.info(header_line)
        logger.info(sep_line)

        for s in summary:
            group_val = str(s["group"])
            if len(group_val) > 19:
                group_val = group_val[:18] + "…"
            row = [
                group_val.ljust(20),
                str(s["count"]).ljust(6),
                str(s["unique_versions"]).ljust(6),
                (s["platforms"][:17] if len(s["platforms"]) > 18 else s["platforms"]).ljust(18),
                (s["channels"][:15] if len(s["channels"]) > 16 else s["channels"]).ljust(16),
                (s["signatures"][:11] if len(s["signatures"]) > 12 else s["signatures"]).ljust(12),
                s["latest_time"][-19:].ljust(19)
            ]
            logger.info(" | ".join(row))

        logger.info("")
        logger.info("查看单个安装包历史变化:")
        logger.info("  --analyze --history-of <软件名>  查看某个软件的所有整理记录")
        return True

    @staticmethod
    def show_package_history(
        source_dir: Path,
        software_name: str,
        manifest_format: str = "json",
        output_format: str = "table"
    ) -> bool:
        records = PackageOrganizer._load_manifest_records(source_dir, manifest_format)
        if not records:
            logger.warning("清单中没有历史记录")
            return False

        matches = [r for r in records if r["software_name"].lower() == software_name.lower() or software_name.lower() in r["original_filename"].lower()]
        if not matches:
            logger.warning(f"找不到与 \"{software_name}\" 相关的整理记录")
            return True

        matches.sort(key=lambda r: r["execution_time"], reverse=True)

        logger.info(f"{'='*60}")
        logger.info(f"软件历史变化: {software_name} (共 {len(matches)} 条记录)")
        logger.info(f"{'='*60}")

        if output_format == "json":
            print(json.dumps([{
                "时间": m["execution_time"],
                "执行模式": "试运行" if m["dry_run"] else "实际执行",
                "原文件名": m["original_filename"],
                "新文件名": m["new_filename"],
                "版本": m["version"],
                "平台": m["platform"],
                "发行类型": m["release_type"],
                "发行渠道": m["distribution"],
                "架构": m["architecture"],
                "签名状态": m["signature_status"],
                "签名者": m["signer"],
                "是否移动": m["moved"],
                "SHA256": m["sha256_hash"][:32] + "..." if len(m["sha256_hash"]) > 32 else m["sha256_hash"],
            } for m in matches], ensure_ascii=False, indent=2))
            return True

        headers = [
            ("时间", 19), ("模式", 6), ("原文件名", 28), ("新文件名", 28),
            ("版本", 10), ("平台", 7), ("渠道", 8), ("签名", 6)
        ]
        header_line = " | ".join(h.ljust(w) for h, w in headers)
        sep_line = "-+-".join("-" * w for _, w in headers)
        logger.info(header_line)
        logger.info(sep_line)

        sig_map = {"signed": "✓已签", "unsigned": "✗未签", "unknown": "?未知", "not_checked": "未检查"}
        for m in matches:
            orig = m["original_filename"]
            if len(orig) > 27:
                orig = orig[:26] + "…"
            new = m["new_filename"]
            if len(new) > 27:
                new = new[:26] + "…"
            row = [
                m["execution_time"][-19:].ljust(19),
                ("试运行" if m["dry_run"] else "实执行").ljust(6),
                orig.ljust(28),
                new.ljust(28),
                (m["version"][:9] if m["version"] else "-").ljust(10),
                (m["platform"][:6] or "-").ljust(7),
                (m["distribution"][:7] or "-").ljust(8),
                sig_map.get(m["signature_status"], m["signature_status"]).ljust(6)
            ]
            logger.info(" | ".join(row))

        logger.info("")
        logger.info("提示: 使用 --analyze --analyze-group-by 查看不同维度的统计")
        return True

    @staticmethod
    def query_manifest(
        source_dir: Path,
        manifest_format: str = "json",
        platform: Optional[str] = None,
        release_type: Optional[str] = None,
        distribution: Optional[str] = None,
        dry_run_only: Optional[bool] = None,
        signature_status: Optional[str] = None,
        output_format: str = "table"
    ):
        manifest_path = source_dir / f"package_manifest.{manifest_format}"
        if not manifest_path.exists():
            logger.error(f"找不到清单文件: {manifest_path}")
            return False

        logger.info(f"{'='*60}")
        logger.info(f"清单历史查询 ({manifest_format.upper()})")
        logger.info(f"{'='*60}")
        logger.info(f"查询条件:")
        if platform:
            logger.info(f"  平台: {platform}")
        if release_type:
            logger.info(f"  发行类型: {release_type}")
        if distribution:
            logger.info(f"  发行渠道: {distribution}")
        if dry_run_only is not None:
            logger.info(f"  执行模式: {'仅试运行' if dry_run_only else '仅实际执行'}")
        if signature_status:
            logger.info(f"  签名状态: {signature_status}")
        logger.info("")

        try:
            all_records = PackageOrganizer._load_manifest_records(source_dir, manifest_format)
            if not all_records:
                logger.warning("清单中没有历史记录")
                return False

            results = PackageOrganizer._filter_manifest_records(
                all_records, platform, release_type, distribution, dry_run_only, signature_status
            )
            for r in results:
                r["sha256_hash"] = (r.get("sha256_hash", "")[:16] + "...") if r.get("sha256_hash") else ""

            if not results:
                logger.warning("没有找到符合条件的记录")
                return True

            logger.info(f"找到 {len(results)} 条符合条件的记录:")
            logger.info("")

            if output_format == "json":
                print(json.dumps(results, ensure_ascii=False, indent=2))
            elif output_format == "csv":
                import csv
                import io
                if results:
                    output = io.StringIO()
                    writer = csv.DictWriter(output, fieldnames=results[0].keys())
                    writer.writeheader()
                    writer.writerows(results)
                    print(output.getvalue())
            else:
                PackageOrganizer._print_query_table(results)

            logger.info("")
            logger.info(f"共 {len(results)} 条记录")
            return True

        except Exception as e:
            logger.error(f"查询清单失败: {e}")
            return False

    @staticmethod
    def _print_query_table(results: list):
        if not results:
            return

        sig_map = {
            "signed": "✓已签",
            "unsigned": "✗未签",
            "unknown": "?未知",
            "not_checked": "未检查"
        }

        headers = [
            ("时间", 19), ("软件名", 16), ("版本", 14), ("平台", 7),
            ("发行类型", 8), ("渠道", 8), ("架构", 7), ("大小", 9),
            ("签名", 6), ("原文件名", 28)
        ]

        header_line = " | ".join(h.ljust(w) for h, w in headers)
        sep_line = "-+-".join("-" * w for _, w in headers)
        logger.info(header_line)
        logger.info(sep_line)

        for r in results:
            mode = "试运行" if r["dry_run"] else "实执行"
            sig = sig_map.get(r["signature_status"], r["signature_status"])
            row = [
                r["execution_time"][-19:].ljust(19),
                (r["software_name"][:15] + "…") if len(r["software_name"]) > 16 else r["software_name"].ljust(16),
                (r["version"][:13] + "…") if len(r["version"]) > 14 else r["version"].ljust(14),
                (r["platform"][:6] or "-").ljust(7),
                (r["release_type"][:7] or "-").ljust(8),
                (r["distribution"][:7] or "-").ljust(8),
                (r["architecture"][:6] or "-").ljust(7),
                r["file_size_human"].ljust(9),
                sig.ljust(6),
                (r["original_filename"][:27] + "…") if len(r["original_filename"]) > 28 else r["original_filename"].ljust(28),
            ]
            logger.info(" | ".join(row))

    def _generate_manifest(self):
        manifest_path = self.source_dir / f"package_manifest.{self.manifest_format}"

        execution_record = {
            "execution_time": self.execution_time,
            "dry_run": self.dry_run,
            "source_directory": str(self.source_dir),
            "target_directory": str(self.target_dir),
            "statistics": self.stats,
            "packages": [],
            "excluded_files": []
        }

        for pkg in self.packages:
            pkg_data = {
                "original_filename": pkg.original_path.name,
                "original_path": str(pkg.original_path),
                "new_filename": pkg.new_filename or pkg.original_path.name,
                "software_name": pkg.software_name,
                "version": pkg.version,
                "architecture": pkg.arch,
                "platform": pkg.platform,
                "release_type": pkg.release_type,
                "distribution": pkg.distribution,
                "package_type": pkg.package_type,
                "file_extension": pkg.file_ext,
                "file_size": pkg.file_size,
                "file_size_human": self._format_size(pkg.file_size),
                "sha256_hash": pkg.sha256_hash,
                "signature": {
                    "status": pkg.signature_status,
                    "details": pkg.signature_details
                },
                "target_directory": str(pkg.target_dir),
                "target_path": str(pkg.target_path),
                "renamed": pkg.renamed,
                "moved": pkg.moved,
                "skipped": pkg.skipped,
                "skip_reason": pkg.skip_reason
            }
            execution_record["packages"].append(pkg_data)

        for ef in self.excluded_files:
            ef_data = {
                "filename": ef.original_path.name,
                "original_path": str(ef.original_path),
                "file_size": ef.file_size,
                "file_size_human": self._format_size(ef.file_size),
                "exclude_rule": ef.exclude_rule,
                "exclude_detail": ef.exclude_detail,
            }
            execution_record["excluded_files"].append(ef_data)

        try:
            if self.manifest_format == "json":
                self._write_json_manifest(manifest_path, execution_record)
            elif self.manifest_format == "csv":
                self._write_csv_manifest(manifest_path, execution_record)
            if self.dry_run:
                logger.info(f"✓ [试运行] 清单已记录: {manifest_path.name}")
            else:
                logger.info(f"✓ 清单已生成: {manifest_path.name}")
        except Exception as e:
            logger.error(f"✗ 清单生成失败: {e}")

    def _write_json_manifest(self, manifest_path: Path, execution_record: dict):
        manifest_data = {
            "manifest_version": "2.0",
            "last_updated": self._get_current_timestamp(),
            "execution_history": []
        }

        if self.append_manifest and manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                if isinstance(existing_data, dict) and "execution_history" in existing_data:
                    manifest_data = existing_data
                else:
                    manifest_data["execution_history"].append({
                        "execution_time": "legacy",
                        "note": "旧版清单数据，已迁移到新版格式",
                        "legacy_data": existing_data
                    })
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"无法读取现有清单，将创建新清单: {e}")

        manifest_data["last_updated"] = self._get_current_timestamp()
        manifest_data["execution_history"].insert(0, execution_record)

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)

    def _write_csv_manifest(self, manifest_path: Path, execution_record: dict):
        import csv
        headers = [
            "执行时间", "执行模式", "软件名", "版本", "发行类型", "发行渠道", "打包类型",
            "架构", "平台", "原文件名", "新文件名", "文件大小", "SHA256",
            "签名状态", "签名者", "是否重命名", "是否移动", "备注"
        ]

        file_exists = self.append_manifest and manifest_path.exists()
        mode = "a" if file_exists else "w"

        with open(manifest_path, mode, encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(headers)

            mode_cn = "试运行" if execution_record["dry_run"] else "实际执行"
            for pkg in execution_record["packages"]:
                signer = ""
                if pkg["signature"]["details"]:
                    signer = pkg["signature"]["details"].get("signer", "")
                status_cn = {
                    "signed": "已签名",
                    "unsigned": "未签名",
                    "unknown": "无法验证",
                    "not_checked": "未检查"
                }.get(pkg["signature"]["status"], pkg["signature"]["status"])
                writer.writerow([
                    execution_record["execution_time"],
                    mode_cn,
                    pkg["software_name"], pkg["version"],
                    pkg.get("release_type", ""), pkg.get("distribution", ""),
                    pkg.get("package_type", ""),
                    pkg["architecture"], pkg["platform"],
                    pkg["original_filename"], pkg["new_filename"],
                    pkg["file_size_human"], pkg["sha256_hash"],
                    status_cn, signer,
                    "是" if pkg["renamed"] else "否",
                    "是" if pkg["moved"] else "否",
                    pkg["skip_reason"]
                ])

            for ef in execution_record.get("excluded_files", []):
                writer.writerow([
                    execution_record["execution_time"],
                    mode_cn,
                    "", "", "", "", "", "", "",
                    ef["filename"], "(已排除)",
                    ef["file_size_human"], "",
                    "", "", "", "", "",
                    f"{ef['exclude_rule']}: {ef['exclude_detail']}"
                ])

    def _get_current_timestamp(self) -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_size(size_str: str) -> int:
    size_str = size_str.strip().upper()
    units = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
        "K": 1024,
        "M": 1024 * 1024,
        "G": 1024 * 1024 * 1024,
    }
    for unit in sorted(units.keys(), key=len, reverse=True):
        if size_str.endswith(unit):
            try:
                value = float(size_str[:-len(unit)])
                return int(value * units[unit])
            except ValueError:
                pass
    try:
        return int(size_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"无效的大小格式: {size_str}，请使用如 10MB、1GB 等格式")


def get_default_config() -> dict:
    return {
        "config_version": "2.0",
        "default_profile": "default",
        "source_directory": ".",
        "target_directory": None,
        "options": {
            "dry_run": False,
            "verify_signatures": True,
            "generate_manifest": True,
            "manifest_format": "json",
            "append_manifest": True,
            "name_include_channel": True,
        },
        "naming": {
            "name_template": "{name}-{version}-{release_type}{distribution}{package_type}-{platform}-{arch}",
            "custom_platform_dirs": {
                "windows": "Windows",
                "macos": "macOS",
                "linux": "Linux",
                "unknown": "Unknown"
            }
        },
        "exclude_rules": {
            "filename_patterns": [
                r"temp",
                r"tmp",
                r"\.crdownload$",
                r"\.part$",
                r"\.download$"
            ],
            "extensions": [],
            "subdirectories": [
                "incomplete",
                "temp",
                "tmp"
            ],
            "min_size": None,
            "max_size": None
        },
        "profiles": {
            "_comment": "多套配置档案，运行时用 --profile 指定。每套可独立覆盖 default 的 target/options/naming/exclude_rules",
            "work": {
                "target_directory": None,
                "options": {
                    "manifest_format": "json",
                    "name_include_channel": True,
                },
                "naming": {
                    "name_template": "{name}-{version}-{platform}-{arch}"
                },
                "exclude_rules": {
                    "filename_patterns": [],
                    "subdirectories": ["incomplete"]
                }
            },
            "personal": {
                "target_directory": None,
                "options": {
                    "manifest_format": "json",
                    "name_include_channel": True,
                },
                "naming": {
                    "name_template": "{name}-{version}-{release_type}{distribution}-{platform}-{arch}"
                },
                "exclude_rules": {
                    "subdirectories": ["incomplete", "temp"]
                }
            },
            "archive": {
                "target_directory": None,
                "options": {
                    "manifest_format": "csv",
                    "append_manifest": True,
                },
                "naming": {
                    "name_template": "{name}-{version}-{distribution}{package_type}-{platform}-{arch}"
                },
                "exclude_rules": {
                    "min_size": None,
                    "max_size": None
                }
            }
        }
    }


def generate_sample_config(config_path: Path) -> bool:
    sample_config = get_default_config()
    sample_config["_comment"] = (
        "安装包整理工具配置文件 v2.0 - 所有设置都可以被命令行参数覆盖\n"
        "name_template 可用变量: {name}软件名 {version}版本号 {platform}平台 {arch}架构\n"
        "                      {release_type}发行类型(stable/beta/rc等) {distribution}发行渠道(portable/offline等) {package_type}打包类型"
    )
    sample_config["naming"]["_comment_template"] = "默认模板: {name}-{version}-{release_type}{distribution}{package_type}-{platform}-{arch}"
    sample_config["naming"]["_comment_vars"] = list(TEMPLATE_VARS.keys())
    sample_config["exclude_rules"]["_comment_patterns"] = "支持正则表达式，匹配文件名"
    sample_config["exclude_rules"]["_comment_subdirs"] = "只匹配完整的子目录名，不会部分匹配"
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(sample_config, f, ensure_ascii=False, indent=2)
        logger.info(f"✓ 已生成示例配置文件: {config_path}")
        logger.info(f"  可用档案: work, personal, archive 或自定义")
        logger.info(f"  命名模板变量: {', '.join(TEMPLATE_VARS.keys())}")
        return True
    except Exception as e:
        logger.error(f"生成配置文件失败: {e}")
        return False


def load_config(config_path: Optional[Path]) -> Optional[dict]:
    if not config_path or not config_path.exists():
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        config_str = str(config_path).lower()
        if config_str.endswith(".yaml") or config_str.endswith(".yml"):
            if not YAML_AVAILABLE:
                logger.warning("未安装 PyYAML，请运行: pip install pyyaml")
                logger.warning("将尝试使用 JSON 解析")
            else:
                    return yaml.safe_load(content)

        return json.loads(content)
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")
        return None


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k.startswith("_"):
            continue
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def merge_config_with_args(config: Optional[dict], args) -> dict:
    if not config:
        return {}

    result = {}

    profile_name = getattr(args, "profile", None) or config.get("default_profile", "default")
    profiles = config.get("profiles", {})

    if profile_name != "default" and profile_name in profiles:
        logger.info(f"应用配置档案: {profile_name}")
        profile_cfg = profiles[profile_name]
        effective_cfg = _deep_merge(dict(config), profile_cfg)
    else:
        effective_cfg = config

    result["profile_name"] = profile_name

    source_dir = effective_cfg.get("source_directory", ".")
    if getattr(args, "directory", None) and args.directory != ".":
        source_dir = args.directory
    result["source_dir"] = Path(source_dir)

    target_dir_cfg = effective_cfg.get("target_directory")
    if target_dir_cfg:
        result["target_dir"] = Path(target_dir_cfg)

    options = effective_cfg.get("options", {})

    result["dry_run"] = bool(args.dry_run) or bool(options.get("dry_run", False))

    if getattr(args, "no_verify_signatures", False):
        result["verify_signatures"] = False
    else:
        result["verify_signatures"] = bool(options.get("verify_signatures", True))

    if getattr(args, "no_manifest", False):
        result["generate_manifest"] = False
    else:
        result["generate_manifest"] = bool(options.get("generate_manifest", True))

    if getattr(args, "manifest_format", "json") != "json":
        result["manifest_format"] = args.manifest_format
    else:
        result["manifest_format"] = options.get("manifest_format", "json")

    if getattr(args, "no_append_manifest", False):
        result["append_manifest"] = False
    else:
        result["append_manifest"] = bool(options.get("append_manifest", True))

    if getattr(args, "no_channel_in_name", False):
        result["name_include_channel"] = False
    else:
        result["name_include_channel"] = bool(options.get("name_include_channel", True))

    naming = effective_cfg.get("naming", {})
    custom_dirs = naming.get("custom_platform_dirs", {})
    if custom_dirs:
        result["custom_platform_dirs"] = custom_dirs

    if getattr(args, "name_template", None):
        result["name_template"] = args.name_template
    else:
        result["name_template"] = naming.get("name_template", DEFAULT_NAME_TEMPLATE)

    exclude_rules = effective_cfg.get("exclude_rules", {})

    exclude_patterns = list(exclude_rules.get("filename_patterns", []))
    if args.exclude:
        exclude_patterns.extend(args.exclude)
    result["exclude_patterns"] = exclude_patterns

    exclude_exts = list(exclude_rules.get("extensions", []))
    if args.exclude_ext:
        exclude_exts.extend(args.exclude_ext)
    result["exclude_exts"] = exclude_exts

    exclude_subdirs = list(exclude_rules.get("subdirectories", []))
    if args.exclude_subdir:
        exclude_subdirs.extend(args.exclude_subdir)
    result["exclude_subdirs"] = exclude_subdirs

    min_size = exclude_rules.get("min_size")
    if args.min_size is not None:
        min_size = args.min_size
    elif isinstance(min_size, str):
        min_size = parse_size(min_size)
    result["min_size"] = min_size

    max_size = exclude_rules.get("max_size")
    if args.max_size is not None:
        max_size = args.max_size
    elif isinstance(max_size, str):
        max_size = parse_size(max_size)
    result["max_size"] = max_size

    return result


def main():
    parser = argparse.ArgumentParser(
        description="安装包整理工具 - 自动分类、重命名和校验下载的安装包",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
配置文件:
  默认会自动在以下位置查找配置:
    1. 源目录下的 package_organizer_config.json
    2. 用户主目录下的 .package_organizer_config.json
  使用 -c/--config 指定自定义配置文件
  使用 --generate-config 生成示例配置

文件名模板变量 (--name-template 或在配置文件中 naming.name_template):
  {name}软件名  {version}版本号  {platform}平台  {arch}架构
  {release_type}发行类型(stable/beta/rc等)
  {distribution}发行渠道(portable/offline等)
  {package_type}打包类型(setup/bundle等)
  例: {name}-{version}-{platform}-{arch}  -> 软件-1.0-Windows-x64.exe

示例:
  # 整理当前目录下的安装包（自动使用配置文件）
  python package_organizer.py

  # 生成示例配置文件（含 work/personal/archive 档案）
  python package_organizer.py --generate-config

  # 使用指定配置档案
  python package_organizer.py --profile work -d ~/Downloads

  # 自定义命名模板并试运行预览
  python package_organizer.py -d ~/Downloads --dry-run \\
      --name-template "{version}-{name}-{platform}-{arch}"

  # 临时关闭渠道信息（命令行优先级最高）
  python package_organizer.py -d ~/Downloads --no-channel-in-name

  # 撤销上次整理（试运行预览）
  python package_organizer.py -d ~/Downloads --undo

  # 真正执行撤销
  python package_organizer.py -d ~/Downloads --undo --execute-undo

  # 查询清单历史（筛选未签名的 Windows 安装包）
  python package_organizer.py -d ~/Downloads --query \\
      --filter-platform Windows --filter-signature unsigned

  # 查询结果导出 JSON
  python package_organizer.py -d ~/Downloads --query \\
      --filter-channel portable --query-output json
        """
    )

    parser.add_argument(
        "-d", "--directory",
        default=".",
        help="要整理的目录路径 (默认: 当前目录)"
    )

    parser.add_argument(
        "-c", "--config",
        default=None,
        help="配置文件路径 (JSON 或 YAML 格式)"
    )

    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="在指定目录生成示例配置文件并退出"
    )

    parser.add_argument(
        "--no-config",
        action="store_true",
        help="不自动加载配置文件，只使用命令行参数"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式，不实际移动文件，仅显示预览报告"
    )

    parser.add_argument(
        "--exclude",
        nargs="+",
        default=[],
        help="按文件名排除（支持正则表达式，追加到配置规则）"
    )

    parser.add_argument(
        "--exclude-ext",
        nargs="+",
        default=[],
        help="按扩展名排除，如 .msi .deb"
    )

    parser.add_argument(
        "--exclude-subdir",
        nargs="+",
        default=[],
        help="按子目录排除（完整匹配目录名），如 temp incomplete"
    )

    parser.add_argument(
        "--min-size",
        type=parse_size,
        default=None,
        help="最小文件大小，如 1MB、100KB"
    )

    parser.add_argument(
        "--max-size",
        type=parse_size,
        default=None,
        help="最大文件大小，如 1GB、500MB"
    )

    parser.add_argument(
        "--target-dir",
        default=None,
        help="整理后的目标目录（默认与源目录相同）"
    )

    parser.add_argument(
        "--no-verify-signatures",
        action="store_true",
        help="跳过数字签名验证"
    )

    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="不生成清单文件"
    )

    parser.add_argument(
        "--no-append-manifest",
        action="store_true",
        help="覆盖模式，不追加到历史记录"
    )

    parser.add_argument(
        "--manifest-format",
        choices=["json", "csv"],
        default="json",
        help="清单文件格式 (默认: json)"
    )

    parser.add_argument(
        "--no-channel-in-name",
        action="store_true",
        help="文件名中不包含发行渠道/类型信息（命令行优先级高于配置文件）"
    )

    parser.add_argument(
        "--name-template",
        default=None,
        help="自定义文件名模板，如 \"{name}-{version}-{platform}-{arch}\""
    )

    parser.add_argument(
        "--profile",
        default=None,
        help="使用配置文件中的某个档案(如 work/personal/archive)"
    )

    undo_group = parser.add_argument_group("撤销操作")
    undo_group.add_argument(
        "--undo",
        action="store_true",
        help="撤销某次整理的文件移动（默认试运行，加 --execute-undo 真正执行）"
    )
    undo_group.add_argument(
        "--undo-list",
        action="store_true",
        help="列出最近的整理任务，可选其中一次撤销"
    )
    undo_group.add_argument(
        "--execute-undo",
        action="store_true",
        help="配合 --undo 真正执行撤销，否则只试运行"
    )
    undo_group.add_argument(
        "--undo-index",
        type=int,
        default=0,
        help="撤销第 N 条历史记录（默认0，从最新的开始）"
    )
    undo_group.add_argument(
        "--undo-files",
        default=None,
        help="只撤销指定的文件序号（逗号分隔，如 0,2,3），默认撤销该批次全部"
    )

    query_group = parser.add_argument_group("清单查询")
    query_group.add_argument(
        "--query",
        action="store_true",
        help="进入查询模式，读取清单历史并按条件筛选输出"
    )
    query_group.add_argument(
        "--filter-platform",
        default=None,
        help="筛选: 平台 (Windows/macOS/Linux)"
    )
    query_group.add_argument(
        "--filter-release-type",
        default=None,
        help="筛选: 发行类型 (stable/beta/rc 等)"
    )
    query_group.add_argument(
        "--filter-channel",
        default=None,
        help="筛选: 发行渠道 (portable/offline/full 等)"
    )
    query_group.add_argument(
        "--filter-dry-run-only",
        choices=["true", "false"],
        default=None,
        help="筛选: 执行模式 (true=仅试运行, false=仅实际执行)"
    )
    query_group.add_argument(
        "--filter-signature",
        choices=["signed", "unsigned", "unknown"],
        default=None,
        help="筛选: 签名状态 (signed/unsigned/unknown)"
    )
    query_group.add_argument(
        "--query-output",
        choices=["table", "json", "csv"],
        default="table",
        help="查询结果输出格式 (默认: table)"
    )

    analyze_group = parser.add_argument_group("历史分析")
    analyze_group.add_argument(
        "--analyze",
        action="store_true",
        help="进入历史分析模式，按维度汇总统计整理历史"
    )
    analyze_group.add_argument(
        "--analyze-group-by",
        choices=["software_name", "platform", "distribution", "release_type", "signature_status"],
        default="software_name",
        help="汇总视图分组依据 (默认: software_name)"
    )
    analyze_group.add_argument(
        "--history-of",
        default=None,
        help="查看某个安装包/软件的所有整理历史（按原名或软件名匹配）"
    )
    analyze_group.add_argument(
        "--analyze-output",
        choices=["table", "json", "csv"],
        default="table",
        help="分析结果输出格式 (默认: table)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细调试信息"
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    target_source_dir = Path(args.directory).expanduser().resolve()

    if args.generate_config:
        config_path = target_source_dir / DEFAULT_CONFIG_FILENAME
        if config_path.exists():
            logger.warning(f"配置文件已存在: {config_path}")
            overwrite = input("是否覆盖? (y/N): ").strip().lower()
            if overwrite != "y":
                logger.info("取消生成配置文件")
                sys.exit(0)
        generate_sample_config(config_path)
        sys.exit(0)

    config = None
    if not args.no_config:
        config_candidates = []
        if args.config:
            config_candidates.append(Path(args.config).expanduser().resolve())
        else:
            config_candidates.append(target_source_dir / DEFAULT_CONFIG_FILENAME)
            config_candidates.append(Path.home() / f".{DEFAULT_CONFIG_FILENAME}")

        for config_path in config_candidates:
            if config_path.exists():
                logger.info(f"加载配置文件: {config_path}")
                config = load_config(config_path)
                if config:
                    break

    merged = merge_config_with_args(config, args)

    manifest_format_effective = args.manifest_format
    if manifest_format_effective == "json" and merged and merged.get("manifest_format"):
        manifest_format_effective = merged["manifest_format"]

    if args.undo_list:
        PackageOrganizer.list_undo_history(target_source_dir, limit=20)
        sys.exit(0)

    if args.undo:
        undo_dry_run = not args.execute_undo
        file_indices = None
        if args.undo_files:
            try:
                file_indices = [int(x.strip()) for x in args.undo_files.split(",") if x.strip()]
            except ValueError:
                logger.error("--undo-files 必须是逗号分隔的整数序号，如 0,2,3")
                sys.exit(1)
        PackageOrganizer.undo_last_operation(
            target_source_dir,
            dry_run=undo_dry_run,
            index=args.undo_index,
            file_indices=file_indices
        )
        sys.exit(0)

    if args.query:
        dry_run_only = None
        if args.filter_dry_run_only == "true":
            dry_run_only = True
        elif args.filter_dry_run_only == "false":
            dry_run_only = False

        ok = PackageOrganizer.query_manifest(
            target_source_dir,
            manifest_format=manifest_format_effective,
            platform=args.filter_platform,
            release_type=args.filter_release_type,
            distribution=args.filter_channel,
            dry_run_only=dry_run_only,
            signature_status=args.filter_signature,
            output_format=args.query_output
        )
        sys.exit(0 if ok else 1)

    if args.history_of:
        PackageOrganizer.show_package_history(
            target_source_dir,
            software_name=args.history_of,
            manifest_format=manifest_format_effective,
            output_format=args.analyze_output
        )
        sys.exit(0)

    if args.analyze:
        dry_run_only = None
        if args.filter_dry_run_only == "true":
            dry_run_only = True
        elif args.filter_dry_run_only == "false":
            dry_run_only = False

        PackageOrganizer.analyze_manifest_history(
            target_source_dir,
            manifest_format=manifest_format_effective,
            platform=args.filter_platform,
            release_type=args.filter_release_type,
            distribution=args.filter_channel,
            dry_run_only=dry_run_only,
            signature_status=args.filter_signature,
            output_format=args.analyze_output,
            group_by=args.analyze_group_by
        )
        sys.exit(0)

    organizer_kwargs: dict = {}
    if merged:
        organizer_kwargs.update({
            "source_dir": merged.get("source_dir", target_source_dir),
            "dry_run": merged.get("dry_run", False),
            "verify_signatures": merged.get("verify_signatures", True),
            "generate_manifest": merged.get("generate_manifest", True),
            "manifest_format": merged.get("manifest_format", "json"),
            "append_manifest": merged.get("append_manifest", True),
            "name_include_channel": merged.get("name_include_channel", True),
            "name_template": merged.get("name_template", DEFAULT_NAME_TEMPLATE),
            "profile_name": merged.get("profile_name", "default"),
            "custom_platform_dirs": merged.get("custom_platform_dirs", {}),
            "exclude_patterns": merged.get("exclude_patterns", []),
            "exclude_exts": merged.get("exclude_exts", []),
            "exclude_subdirs": merged.get("exclude_subdirs", []),
            "min_size": merged.get("min_size"),
            "max_size": merged.get("max_size"),
        })
        if merged.get("target_dir"):
            organizer_kwargs["target_dir"] = merged["target_dir"]

    if args.dry_run:
        organizer_kwargs["dry_run"] = True
    if args.no_verify_signatures:
        organizer_kwargs["verify_signatures"] = False
    if args.no_manifest:
        organizer_kwargs["generate_manifest"] = False
    if args.no_append_manifest:
        organizer_kwargs["append_manifest"] = False
    if args.no_channel_in_name:
        organizer_kwargs["name_include_channel"] = False
    if args.manifest_format != "json":
        organizer_kwargs["manifest_format"] = args.manifest_format
    if args.name_template:
        organizer_kwargs["name_template"] = args.name_template
    if args.target_dir:
        organizer_kwargs["target_dir"] = Path(args.target_dir).expanduser().resolve()
    if args.exclude:
        existing = organizer_kwargs.get("exclude_patterns", [])
        organizer_kwargs["exclude_patterns"] = list(existing) + list(args.exclude)
    if args.exclude_ext:
        existing = organizer_kwargs.get("exclude_exts", [])
        organizer_kwargs["exclude_exts"] = list(existing) + list(args.exclude_ext)
    if args.exclude_subdir:
        existing = organizer_kwargs.get("exclude_subdirs", [])
        organizer_kwargs["exclude_subdirs"] = list(existing) + list(args.exclude_subdir)
    if args.min_size is not None:
        organizer_kwargs["min_size"] = args.min_size
    if args.max_size is not None:
        organizer_kwargs["max_size"] = args.max_size

    organizer_kwargs.setdefault("source_dir", target_source_dir)
    organizer_kwargs.setdefault("dry_run", False)
    organizer_kwargs.setdefault("verify_signatures", True)
    organizer_kwargs.setdefault("generate_manifest", True)
    organizer_kwargs.setdefault("manifest_format", "json")
    organizer_kwargs.setdefault("append_manifest", True)
    organizer_kwargs.setdefault("name_include_channel", True)
    organizer_kwargs.setdefault("name_template", DEFAULT_NAME_TEMPLATE)
    organizer_kwargs.setdefault("profile_name", "default")
    organizer_kwargs.setdefault("custom_platform_dirs", {})
    organizer_kwargs.setdefault("exclude_patterns", [])
    organizer_kwargs.setdefault("exclude_exts", [])
    organizer_kwargs.setdefault("exclude_subdirs", [])

    try:
        organizer = PackageOrganizer(**organizer_kwargs)
        organizer.run()
    except KeyboardInterrupt:
        logger.info("\n操作已取消")
        sys.exit(1)


if __name__ == "__main__":
    main()
