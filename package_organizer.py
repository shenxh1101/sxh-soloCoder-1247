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
        custom_platform_dirs: Optional[dict] = None
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
        self.custom_platform_dirs = custom_platform_dirs or {}
        self.execution_time = self._get_current_timestamp()
        self.packages: list[PackageInfo] = []
        self.excluded_files: list[ExcludedFileInfo] = []
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
        logger.info(f"执行时间: {self.execution_time}")
        logger.info(f"试运行模式: {'开启' if self.dry_run else '关闭'}")
        logger.info(f"签名验证: {'开启' if self.verify_signatures else '关闭'}")
        logger.info(f"清单追加模式: {'开启' if self.append_manifest else '关闭'}")
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

    def _prepare_target_path(self, pkg: PackageInfo):
        name_parts = []
        if pkg.software_name:
            name_parts.append(pkg.software_name)
        if pkg.version:
            name_parts.append(pkg.version)
        if not name_parts:
            name_parts.append(pkg.original_path.stem)

        base_name = "-".join(name_parts)
        base_name = re.sub(r"[-_]+", "-", base_name)

        if self.name_include_channel:
            channel_parts = []
            if pkg.release_type and pkg.release_type != "stable":
                channel_parts.append(pkg.release_type)
            if pkg.distribution:
                channel_parts.append(pkg.distribution)
            if pkg.package_type and pkg.package_type != "setup":
                channel_parts.append(pkg.package_type)
            if channel_parts:
                base_name = base_name + "-" + "-".join(channel_parts)

        platform_label = PLATFORM_LABELS.get(pkg.platform, "Unknown")

        existing_platform = None
        for label in PLATFORM_LABELS.values():
            if re.search(rf"[-_]{label}[-_]?\b", base_name, re.IGNORECASE):
                existing_platform = label
                base_name = re.sub(rf"[-_]{label}[-_]?\b", "-", base_name, flags=re.IGNORECASE)
                base_name = re.sub(r"[-_]+", "-", base_name).strip(" -")
                break

        if existing_platform:
            platform_label = existing_platform

        if pkg.arch:
            new_name = f"{base_name}-{platform_label}-{pkg.arch}{pkg.file_ext}"
        else:
            new_name = f"{base_name}-{platform_label}{pkg.file_ext}"

        new_name = re.sub(r"-{2,}", "-", new_name)
        pkg.target_dir = self._get_platform_dir(pkg.platform)

        pkg.new_filename = new_name
        pkg.target_path = pkg.target_dir / new_name

        counter = 1
        while pkg.target_path.exists() and pkg.target_path.resolve() != pkg.original_path.resolve():
            channel_suffix = "-" + "-".join(channel_parts) if (self.name_include_channel and channel_parts) else ""
            if pkg.arch:
                new_name = f"{base_name}{channel_suffix}-{platform_label}-{pkg.arch}-{counter}{pkg.file_ext}"
            else:
                new_name = f"{base_name}{channel_suffix}-{platform_label}-{counter}{pkg.file_ext}"
            new_name = re.sub(r"-{2,}", "-", new_name)
            pkg.target_path = pkg.target_dir / new_name
            pkg.new_filename = new_name
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
        "config_version": "1.0",
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
            "include_platform": True,
            "include_architecture": True,
            "include_channel": True,
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
        }
    }


def generate_sample_config(config_path: Path) -> bool:
    sample_config = get_default_config()
    sample_config["_comment"] = "安装包整理工具配置文件 - 所有设置都可以被命令行参数覆盖"
    sample_config["exclude_rules"]["_comment_patterns"] = "支持正则表达式，匹配文件名"
    sample_config["exclude_rules"]["_comment_subdirs"] = "只匹配完整的子目录名，不会部分匹配"
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(sample_config, f, ensure_ascii=False, indent=2)
        logger.info(f"✓ 已生成示例配置文件: {config_path}")
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


def merge_config_with_args(config: Optional[dict], args) -> dict:
    if not config:
        return {}

    result = {}

    source_dir = config.get("source_directory", ".")
    if getattr(args, "directory", None) and args.directory != ".":
        source_dir = args.directory
    result["source_dir"] = Path(source_dir)

    target_dir = config.get("target_directory")
    if target_dir:
        result["target_dir"] = Path(target_dir)

    options = config.get("options", {})
    result["dry_run"] = args.dry_run or options.get("dry_run", False)
    result["verify_signatures"] = not args.no_verify_signatures and options.get("verify_signatures", True)
    result["generate_manifest"] = not args.no_manifest and options.get("generate_manifest", True)
    result["manifest_format"] = args.manifest_format if args.manifest_format != "json" else options.get("manifest_format", "json")
    result["append_manifest"] = not args.no_append_manifest and options.get("append_manifest", True)
    result["name_include_channel"] = options.get("name_include_channel", True)

    naming = config.get("naming", {})
    custom_dirs = naming.get("custom_platform_dirs", {})
    if custom_dirs:
        result["custom_platform_dirs"] = custom_dirs

    exclude_rules = config.get("exclude_rules", {})

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

示例:
  # 整理当前目录下的安装包（自动使用配置文件）
  python package_organizer.py

  # 生成示例配置文件
  python package_organizer.py --generate-config

  # 使用指定配置文件
  python package_organizer.py -c my_config.json

  # 整理指定目录，试运行模式（预览效果）
  python package_organizer.py -d ~/Downloads --dry-run

  # 命令行参数会覆盖配置文件中的设置
  python package_organizer.py -c config.json -d ~/Downloads --dry-run

  # 按文件名排除（支持正则，会追加到配置中的规则）
  python package_organizer.py -d ~/Downloads --exclude temp test

  # 按扩展名排除
  python package_organizer.py -d ~/Downloads --exclude-ext .msi .deb

  # 按子目录排除（只匹配完整目录名，不会部分匹配）
  python package_organizer.py -d ~/Downloads --exclude-subdir temp incomplete

  # 按文件大小过滤（小于10MB或大于5GB的跳过）
  python package_organizer.py -d ~/Downloads --min-size 10MB --max-size 5GB

  # 生成 CSV 格式的清单
  python package_organizer.py -d ~/Downloads --manifest-format csv

  # 覆盖模式（不追加到历史记录）
  python package_organizer.py -d ~/Downloads --no-append-manifest

  # 跳过签名验证
  python package_organizer.py -d ~/Downloads --no-verify-signatures

  # 组合使用多个排除规则
  python package_organizer.py -d ~/Downloads --dry-run \\
      --exclude temp old \\
      --exclude-ext .msi \\
      --exclude-subdir incomplete \\
      --min-size 1MB
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
        help="文件名中不包含发行渠道/类型信息"
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

    organizer_kwargs = {
        "source_dir": merged.get("source_dir", target_source_dir),
        "dry_run": args.dry_run,
        "exclude_patterns": args.exclude,
        "exclude_exts": args.exclude_ext,
        "exclude_subdirs": args.exclude_subdir,
        "min_size": args.min_size,
        "max_size": args.max_size,
        "verify_signatures": not args.no_verify_signatures,
        "generate_manifest": not args.no_manifest,
        "manifest_format": args.manifest_format,
        "append_manifest": not args.no_append_manifest,
        "name_include_channel": not args.no_channel_in_name,
    }

    if args.target_dir:
        organizer_kwargs["target_dir"] = Path(args.target_dir).expanduser().resolve()

    if merged:
        for key, value in merged.items():
            if key == "source_dir":
                continue
            if key in ["dry_run", "verify_signatures", "generate_manifest",
                       "append_manifest", "name_include_channel"]:
                if key not in organizer_kwargs or (
                    isinstance(organizer_kwargs.get(key), bool) and not organizer_kwargs[key]
                ):
                    organizer_kwargs[key] = value
            elif key == "custom_platform_dirs":
                organizer_kwargs["custom_platform_dirs"] = value
            elif key in ["exclude_patterns", "exclude_exts", "exclude_subdirs"]:
                if value and key not in organizer_kwargs:
                    organizer_kwargs[key] = value
                elif value and key in organizer_kwargs and not organizer_kwargs[key]:
                    organizer_kwargs[key] = value
            elif key == "target_dir" and "target_dir" not in organizer_kwargs:
                organizer_kwargs["target_dir"] = value
            elif key in ["min_size", "max_size"]:
                if organizer_kwargs.get(key) is None:
                    organizer_kwargs[key] = value

        if "target_dir" in merged and args.target_dir is None:
            organizer_kwargs["target_dir"] = merged["target_dir"]

    try:
        organizer = PackageOrganizer(**organizer_kwargs)
        organizer.run()
    except KeyboardInterrupt:
        logger.info("\n操作已取消")
        sys.exit(1)


if __name__ == "__main__":
    main()
