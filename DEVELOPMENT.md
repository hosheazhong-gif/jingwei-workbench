# 二次开发指南

本文只覆盖运行、测试、扩展和打包。普通用户请阅读 [README.md](README.md) 并下载 Release。

## 技术组成

- Python 3.11+ 标准库后端与本地 HTTP API
- SQLite 单文件数据库与显式迁移
- 原生 HTML、CSS、JavaScript 三栏界面
- JSON 咨询模板
- 无第三方运行时依赖的 Word / Markdown / 纯文本导出
- PyInstaller Windows 单文件封装

运行链路是 `frontend → local HTTP API → application services → SQLite`。模板只定义问题提示、步骤和边界，不绕过应用层写账本。

## 本地运行

```powershell
git clone https://github.com/hosheazhong-gif/jingwei-workbench.git
cd jingwei-workbench
python -m venv .venv
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m app.desktop
```

`app.desktop` 使用 `%LOCALAPPDATA%\Jingwei`。若希望使用独立的开发数据目录：

```powershell
$env:JINGWEI_DATA_DIR="$PWD\var\dev-data"
.\.venv\Scripts\python.exe -m app.desktop
```

也可以直接启动本地服务：

```powershell
.\.venv\Scripts\python.exe -m app.cli --db var\dev.sqlite3 serve --host 127.0.0.1 --port 8000
```

## 目录

- `app/`：领域模型、应用服务、SQLite 适配器、API、模板与导出器
- `frontend/report/`：工作台页面
- `tests/`：单元、集成、HTTP、模板闭环和应用包测试
- `samples/`：自动化测试使用的固定样本
- `packaging/`：PyInstaller 规格与应用包内说明
- `scripts/`：CI 分片和 Windows 构建入口

## 测试

运行完整测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

运行应用入口与封装相关测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_desktop tests.test_create_project -v
```

CI 在 Ubuntu/Python 3.12 和 Windows/Python 3.11 上执行同一套测试。Windows 使用确定性分片减少 GitHub runner 的 SQLite I/O 时间；每项测试只进入一个分片。

## 构建 Windows 应用

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts\build_windows_app.py
```

构建脚本会：

1. 生成 `dist\Jingwei.exe`；
2. 直接运行成品 EXE，验证页面、7 个模板、数据库写入和 Word 导出；
3. 生成 `dist\Jingwei-Windows-x64-v<版本>.zip`；
4. 为 EXE 和 ZIP 生成 SHA-256 文件。

PyInstaller 只能为当前操作系统构建可执行文件，因此 Windows 包必须在 Windows runner 或 Windows 开发机上生成。

## 新增模板

在 `app/templates/<template-name>/template.json` 新增配置。模板至少需要唯一 `template_key`、展示名、适用边界、步骤、参考问题和验证状态。不要在模板里放客户事实、真实项目结论或 API Key。

新增模板后必须运行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_extension_seams tests.test_template_walk -v
```

`test_template_walk` 会让所有正式模板经过同一条受控闭环，避免只在选择器里“看得见”却无法实际使用。

## 数据兼容

- 当前应用版本：0.1.1
- 当前数据库 schema：0.8
- 不要修改已经发布的迁移文件；新增 schema 变更必须添加新的顺序迁移。
- 程序启动时自动迁移用户数据库。任何迁移都要补充旧库升级测试。

## 发布检查

发布前至少确认：

- 完整测试通过；
- `python -m compileall -q app scripts tests` 通过；
- `node --check frontend/report/app.js` 通过；
- Windows 构建脚本及成品 EXE 冒烟测试通过；
- Release 只包含应用包、校验文件和面向用户的更新说明；
- 仓库不包含数据库、材料副本、导出稿、`.env`、API Key 或内部过程文档。
