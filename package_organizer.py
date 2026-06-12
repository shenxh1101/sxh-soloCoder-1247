#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安装包整理工具 - 自动整理下载文件夹中的安装包
"""

import argparse
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

VERSION_PATTERNS = [
    re.compile(r"[-_](\d+\.\d+(?:\.\d+)?(?:\.\d+)?(?:[-_]?\w+)?)"),
    re.compile(r"[vV](\d+\.\d+(?:\.\d+)?(?:\.\d+)?)"),
    re.compile(r"(\d+\.\d+(?:\.\d+)?(?:\.\d+)?(?:[-_]?\w+)?)\.")
]

SETUP_PATTERN = re.compile(r"(setup|installer)", re.IGNORECASE)
NAME_CLEAN_PATTERN = re.compile(r"[-_](?:\d+[.-]|x86|x64|win|mac|linux)", re.IGNORECASE)
ARCH_PATTERN = re.compile(r"(?:[-_])(x86_64|x64|amd64|x86|i386|arm64|aarch64)", re.IGNORECASE)


@dataclass
class PackageInfo:
    original_path: Path
    file_ext: str
    platform: str
    software_name: str = ""
    version: str = ""
    arch: str = ""
    new_filename: str = ""
    target_dir: Path = field(default_factory=Path)
    target_path: Path = field(default_factory=Path)
    sha256_hash: str = ""
    signature_status: str = "not_checked"
    signature_details: Optional[dict] = None
    skipped: bool = False
    skip_reason: str = ""
    moved: bool = False


class PackageOrganizer:
    def __init__(
        self,
        source_dir: Path,
        dry_run: bool = False,
        exclude_patterns: Optional[list] = None,
        verify_signatures: bool = True,
        generate_manifest: bool = True,
        manifest_format: str = "json"
    ):
        self.source_dir = source_dir.expanduser().resolve()
        self.dry_run = dry_run
        self.exclude_patterns = exclude_patterns or []
        self.verify_signatures = verify_signatures
        self.generate_manifest = generate_manifest
        self.manifest_format = manifest_format
        self.packages: list[PackageInfo] = []
        self.stats = {
            "total": 0,
            "moved": 0,
            "skipped": 0,
            "signed": 0,
            "unsigned": 0,
            "sig_unknown": 0,
        }

    def run(self):
        logger.info(f"{'='*60}")
        logger.info(f"安装包整理工具")
        logger.info(f"{'='*60}")
        logger.info(f"源目录: {self.source_dir}")
        logger.info(f"试运行模式: {'开启' if self.dry_run else '关闭'}")
        logger.info(f"签名验证: {'开启' if self.verify_signatures else '关闭'}")
        if self.exclude_patterns:
            logger.info(f"排除模式: {', '.join(self.exclude_patterns)}")
        logger.info("")

        if not self.source_dir.exists():
            logger.error(f"错误: 源目录不存在: {self.source_dir}")
            sys.exit(1)

        self._scan_files()
        self._process_packages()
        self._print_summary()

        if self.generate_manifest:
            self._generate_manifest()

        logger.info("")
        logger.info("完成!")

    def _scan_files(self):
        logger.info("扫描文件中...")
        for filepath in self.source_dir.iterdir():
            if not filepath.is_file():
                continue

            if self._is_excluded(filepath):
                continue

            file_ext = self._get_package_extension(filepath.name)
            if not file_ext:
                continue

            platform = PLATFORM_MAP.get(file_ext.lower(), "unknown")
            pkg = PackageInfo(
                original_path=filepath,
                file_ext=file_ext,
                platform=platform
            )
            self.packages.append(pkg)
            self.stats["total"] += 1

        logger.info(f"找到 {len(self.packages)} 个安装包文件")
        logger.info("")

    def _is_excluded(self, filepath: Path) -> bool:
        filename = filepath.name
        for pattern in self.exclude_patterns:
            if re.search(pattern, filename, re.IGNORECASE):
                return True
        return False

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

        if pkg.arch:
            new_name = f"{base_name}-{pkg.arch}{pkg.file_ext}"
        else:
            new_name = f"{base_name}{pkg.file_ext}"

        platform_dir = PLATFORM_DIRS.get(pkg.platform, "Unknown")
        pkg.target_dir = self.source_dir / platform_dir

        pkg.new_filename = new_name
        pkg.target_path = pkg.target_dir / new_name

        counter = 1
        while pkg.target_path.exists() and pkg.target_path.resolve() != pkg.original_path.resolve():
            if pkg.arch:
                new_name = f"{base_name}-{pkg.arch}-{counter}{pkg.file_ext}"
            else:
                new_name = f"{base_name}-{counter}{pkg.file_ext}"
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

        if self.dry_run:
            logger.info(f"  [试运行] 将移动: {pkg.original_path.name} -> "
                       f"{pkg.target_dir.name}/{pkg.target_path.name}")
            return

        try:
            pkg.target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pkg.original_path), str(pkg.target_path))
            pkg.moved = True
            self.stats["moved"] += 1
            logger.info(f"  ✓ 已移动: {pkg.target_dir.name}/{pkg.target_path.name}")
        except Exception as e:
            pkg.skipped = True
            pkg.skip_reason = f"移动失败: {e}"
            self.stats["skipped"] += 1
            logger.error(f"  ✗ 移动失败: {e}")

    def _print_summary(self):
        logger.info("")
        logger.info(f"{'='*60}")
        logger.info("处理摘要")
        logger.info(f"{'='*60}")
        logger.info(f"总文件数:    {self.stats['total']}")
        logger.info(f"已移动:      {self.stats['moved']}")
        logger.info(f"跳过:        {self.stats['skipped']}")
        if self.verify_signatures:
            logger.info(f"已签名:      {self.stats['signed']}")
            logger.info(f"未签名:      {self.stats['unsigned']}")
            logger.info(f"无法验证:    {self.stats['sig_unknown']}")

    def _generate_manifest(self):
        manifest_path = self.source_dir / f"package_manifest.{self.manifest_format}"

        if self.dry_run:
            logger.info(f"  [试运行] 将生成清单: {manifest_path.name}")
            return

        manifest_data = {
            "generated_at": self._get_current_timestamp(),
            "source_directory": str(self.source_dir),
            "statistics": self.stats,
            "packages": []
        }

        for pkg in self.packages:
            pkg_data = {
                "original_filename": pkg.original_path.name,
                "new_filename": pkg.new_filename or pkg.original_path.name,
                "software_name": pkg.software_name,
                "version": pkg.version,
                "architecture": pkg.arch,
                "platform": pkg.platform,
                "file_extension": pkg.file_ext,
                "sha256_hash": pkg.sha256_hash,
                "signature": {
                    "status": pkg.signature_status,
                    "details": pkg.signature_details
                },
                "target_directory": str(pkg.target_dir),
                "target_path": str(pkg.target_path),
                "moved": pkg.moved,
                "skipped": pkg.skipped,
                "skip_reason": pkg.skip_reason
            }
            manifest_data["packages"].append(pkg_data)

        try:
            if self.manifest_format == "json":
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest_data, f, ensure_ascii=False, indent=2)
            elif self.manifest_format == "csv":
                self._write_csv_manifest(manifest_path, manifest_data)
            logger.info(f"✓ 清单已生成: {manifest_path.name}")
        except Exception as e:
            logger.error(f"✗ 清单生成失败: {e}")

    def _write_csv_manifest(self, manifest_path: Path, manifest_data: dict):
        import csv
        headers = [
            "软件名", "版本", "架构", "平台", "原文件名", "新文件名",
            "SHA256", "签名状态", "签名者", "是否移动", "备注"
        ]
        with open(manifest_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for pkg in manifest_data["packages"]:
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
                    pkg["software_name"], pkg["version"], pkg["architecture"],
                    pkg["platform"], pkg["original_filename"], pkg["new_filename"],
                    pkg["sha256_hash"], status_cn, signer,
                    "是" if pkg["moved"] else "否",
                    pkg["skip_reason"]
                ])

    def _get_current_timestamp(self) -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    parser = argparse.ArgumentParser(
        description="安装包整理工具 - 自动分类、重命名和校验下载的安装包",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 整理当前目录下的安装包
  python package_organizer.py

  # 整理指定目录，试运行模式
  python package_organizer.py -d ~/Downloads --dry-run

  # 排除包含 'temp' 或 'test' 的文件
  python package_organizer.py -d ~/Downloads --exclude temp test

  # 生成 CSV 格式的清单
  python package_organizer.py -d ~/Downloads --manifest-format csv

  # 跳过签名验证
  python package_organizer.py -d ~/Downloads --no-verify-signatures
        """
    )

    parser.add_argument(
        "-d", "--directory",
        default=".",
        help="要整理的目录路径 (默认: 当前目录)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式，不实际移动文件"
    )

    parser.add_argument(
        "--exclude",
        nargs="+",
        default=[],
        help="排除匹配模式的文件（支持正则表达式）"
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
        "--manifest-format",
        choices=["json", "csv"],
        default="json",
        help="清单文件格式 (默认: json)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细调试信息"
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    organizer = PackageOrganizer(
        source_dir=Path(args.directory),
        dry_run=args.dry_run,
        exclude_patterns=args.exclude,
        verify_signatures=not args.no_verify_signatures,
        generate_manifest=not args.no_manifest,
        manifest_format=args.manifest_format
    )

    try:
        organizer.run()
    except KeyboardInterrupt:
        logger.info("\n操作已取消")
        sys.exit(1)


if __name__ == "__main__":
    main()
