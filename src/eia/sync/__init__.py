"""浏览器插件每天把 ChatGPT、Claude 的对话发到本机，eia 写进 vault（docs/specs/chat-sync.md）。

插件只发网站接口返回的原始 JSON；转换、合并全在这里，复用导入官方导出包的代码。
"""
