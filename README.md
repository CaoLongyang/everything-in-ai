# everything-in-ai

把你和 AI 的对话、以后还有你关注的信息，沉淀成一个纯 Markdown 的个人知识库。
数据是一个普通文件夹（vault），用 Obsidian 就能打开，不绑定任何软件。

所有文档一览见 `docs/README.md`。当前进度见 `tasks/todo.md`，各版本的范围见 `docs/ROADMAP.md`。

## 安装（Windows，PowerShell）

需要 Python 3.11 或更高版本。

```powershell
cd $HOME\Desktop\everything-in-ai
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

如果 `Activate.ps1` 被系统拦住，先运行一次：
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## 检查代码是否正常

```powershell
python scripts\check.py
```

最后一行是 `ALL PASSED` 就是全部通过；否则会列出哪一项失败。
每条验收标准的结果在 `reports\acceptance.md`。

## 使用

vault 放在代码文件夹**外面**，比如文档文件夹：

```powershell
eia init $HOME\Documents\my-vault          # 创建 vault
cd $HOME\Documents\my-vault
eia new note --title "第一篇笔记"           # 新建条目
eia validate                                # 检查格式
```

导入 ChatGPT 对话（zip 不用解压）：

```powershell
eia import chatgpt $HOME\Desktop\everything-in-ai\exports\chatgpt_memory.zip --dry-run   # 先预览
eia import chatgpt $HOME\Desktop\everything-in-ai\exports\chatgpt_memory.zip             # 真正导入
```

导入 Claude 对话：Claude 的导出邮件里有 6 个下载链接，只需要其中的 `conversations-000.zip`（同样不用解压）：

```powershell
eia import claude $HOME\Desktop\everything-in-ai\exports\conversations-000.zip --dry-run   # 先预览
eia import claude $HOME\Desktop\everything-in-ai\exports\conversations-000.zip             # 真正导入
eia import claude $HOME\Desktop\everything-in-ai\exports\conversations-000.zip --include-thinking   # 连思考过程一起导入（折叠显示）
```

重复导入是安全的：只更新有新消息的对话，你加的标签和写在 `eia:conversation:end` 标记下面的笔记都会保留。

然后用 Obsidian 的"打开本地仓库"打开 `my-vault` 文件夹。

条目类型：`note` 笔记、`topic` 主题、`clip` 剪藏、`chat` 对话、`digest` 简报、`headline` 大事。
格式规范见 `docs/specs/vault-schema.md`。

## 隐私

- `exports/` 用来放你从各平台导出的原始数据，已经在 `.gitignore` 里，不会进代码仓库。
- 工具不会联网，不会调用任何 AI，不会替你执行 git push。
