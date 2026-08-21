# 音潮 IP 歌曲创作（yinchao-ip-music）

> 当前版本：v1.0.0

**给你的角色、IP、热点话题做一首专属 AI 歌曲——从想法到成曲，3 步完成。**

不用懂音乐，不用写提示词。说出你的目标，它会联网搜索素材、创作歌词与曲风，再交给音潮 AI 生成可播放的完整歌曲（默认 2 个版本任你选）。

## 效果预览

> 输入：给我的小说角色「林晚舟」写一首角色歌，她是一个被家族牺牲又在废墟上重建自己的古风侠女。
>
> 过程：联网搜索角色意象素材 → 创作歌词与曲风 → 音潮 v4.0 生成歌曲
>
> 产出：《林晚舟》· 古风 / 国风配乐 · 完整歌词 + 试听链接 + 本地 MP3

> 📁 **真实示例**：本包内 `凡人修仙传-红尘劫/` 目录即由本 skill 全链路生成的示例——素材卡、创作简报、歌词曲风（`songs/`）与两个版本 MP3（`audio/`），可试听验证实际效果。

## 快速开始（3 步）

1. **安装**：将本目录放入你的 Claude Code 项目，或复制到 skills 目录。
2. **配置 API Key**：在环境中设置环境变量 `YINCHAO_API_KEY`（见下方注册引导）。
3. **说出想法**：告诉 Claude「给 XX 写首歌」即可。默认流程：联网搜索分析 → 歌词曲风 → 生歌（2 个版本）。

## 注册音潮开放平台

> 🎁 **当前限时免费**。本 skill 的音乐生成由[音潮开放平台](https://platform.yinchaoyongxian.com/?register_channel=skillhub_ip)提供，使用前需要注册并创建 API Key：

1. 前往[音潮开放平台](https://platform.yinchaoyongxian.com/?register_channel=skillhub_ip)注册账号
2. 在控制台创建 API Key
3. 在运行当前 Agent 的环境中设置环境变量：
   ```bash
   export YINCHAO_API_KEY="你的 API Key"
   ```

> 安全提示：不要把完整 API Key 发到聊天中；配置在本机即可。

## 三阶段能力

| 阶段 | 做什么 | 产出 |
|---|---|---|
| ① 联网搜索分析 | 自动判断目标类型（角色/IP/热点/曲风/受众），搜索并清洗素材 | 素材卡 + 创作简报 |
| ② 歌词曲风创作 | 核心命题方法论创作有目标指纹的歌词与曲风（5 项自检、Hook、副歌反推） | 歌词 + 曲风描述 |
| ③ AI 生歌 | 音潮 v4.0 生成完整歌曲，默认 2 版本 | 试听链接 + 完整歌词 + 本地 MP3 |

## 使用场景

| 场景 | 示例 |
|---|---|
| 角色歌 | 小说/漫画/游戏角色专属歌曲 |
| 热点话题歌 | 给当前热点事件、社会话题写歌 |
| 品牌 IP 歌 | 品牌、吉祥物、企业主题曲 |
| 风格歌 | 指定曲风（古风/蒸汽波/国潮等）创作 |
| 受众歌 | 面向特定人群表达共鸣的歌 |

也支持只生成歌词与曲风（不需要音频的场景）。

## 分发说明

复制或打包本 skill 时，请排除以下内容（见 `.gitignore`）：

- `__pycache__/`、`*.pyc`——测试运行产生的缓存
- `.task-driver/`——任务驱动工作区，非 skill 内容
- `.env`——本地环境配置

`凡人修仙传-红尘劫/` 为真实链路示例资产，建议随包保留，供用户试听验证效果；**发布到 SkillHub 时请排除其 `audio/` 子目录**（MP3 二进制会使上传请求超限，`skillhub publish` 会报 400 request body too large）。

## 常见问题

**要付费吗？**
当前限时免费。活动期限、可用额度和后续计费以[音潮开放平台](https://platform.yinchaoyongxian.com/?register_channel=skillhub_ip)展示为准。

**API Key 从哪里来？**
在[音潮开放平台](https://platform.yinchaoyongxian.com/?register_channel=skillhub_ip)注册并创建，然后设置环境变量 `YINCHAO_API_KEY`。

**歌曲质量如何？**
音潮 v4.0 模型生成，模型能力（语言支持、曲风理解等）以[音潮开放平台](https://platform.yinchaoyongxian.com/?register_channel=skillhub_ip)展示为准。

**生成的歌曲在哪？**
对话中会给试听链接，同时自动下载 MP3 到本地 `audio/` 文件夹；歌词与曲风保存在 `songs/`。

**安全吗？**
API Key 只保存在本机配置文件，不进聊天记录；本 skill 不会把密钥内容写入日志或上传。

---

由[音潮开放平台](https://platform.yinchaoyongxian.com/?register_channel=skillhub_ip)驱动 · 当前限时免费
