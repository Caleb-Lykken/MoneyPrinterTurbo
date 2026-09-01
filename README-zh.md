<div align="center">

# MoneyPrinterTurbo 💸

### 一站式 AI 短视频生成工具

只需提供视频<b>主题</b>或<b>关键词</b>，即可自动生成视频脚本、匹配素材、生成字幕和背景音乐，并合成高清短视频。

[![Version](https://img.shields.io/github/v/release/harry0703/MoneyPrinterTurbo?color=blue&label=version)](https://github.com/harry0703/MoneyPrinterTurbo/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](https://github.com/harry0703/MoneyPrinterTurbo/releases/latest)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Downloads](https://img.shields.io/github/downloads/harry0703/MoneyPrinterTurbo/total)](https://github.com/harry0703/MoneyPrinterTurbo/releases/latest)

<a href="https://trendshift.io/repositories/8731" target="_blank"><img src="https://trendshift.io/api/badge/repositories/8731" alt="harry0703%2FMoneyPrinterTurbo | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
<a href="https://www.star-history.com/harry0703/moneyprinterturbo"><img src="https://api.star-history.com/badge?repo=harry0703/MoneyPrinterTurbo" alt="Star History Rank" style="height: 55px;" height="55"/></a>

简体中文 | [English](README.md) | [日本語](README-ja.md) | [版本发布](https://github.com/harry0703/MoneyPrinterTurbo/releases) | [问题反馈](https://github.com/harry0703/MoneyPrinterTurbo/issues)

</div>


---

## 本分支的改动 🔱

本分支在上游 MoneyPrinterTurbo 的基础上增加了**批量生产与自动发布**能力。
上游每次运行处理一个主题，本分支接受一份主题清单并端到端跑完整个流程。

**批量生成** —— `--subjects-file topics.txt` 会先为每个主题生成文案并写入可续跑的
批次清单，然后逐条渲染。单个主题失败只会被记录，批次继续执行。任何时候中断后，
用 `--batch-manifest` 重新运行即可：已完成的跳过，失败的重试，不会重复渲染。

**直接发布到 YouTube** —— 通过你自己的 Google Cloud 项目调用 YouTube Data API
上传（`--publish`），不依赖第三方中转。标题、描述和标签由 LLM 生成，每次上传都会
声明 `containsSyntheticMedia`。已经拿到 video id 的条目不会被重复上传。

**批量界面** —— `sh batch-ui.sh` 启动独立的 Streamlit 应用（端口 8601，与主 WebUI
互不影响），可以发起批次并实时查看：每个主题的状态、分配到的音乐与音色、文案、
日志和成片。同时可以查看已发布视频的表现、检索同领域当前热门内容，并据此生成新选题。

**无人值守自动化** —— `scripts/auto_daily.py` 会先补发积压，再检索同领域趋势并渲染
新选题。渲染与发布可以分别调度，从而把上传分散到全天，而不是集中在同一时间。

**面向英文 Shorts 的输出默认值** —— 每个主题随机选用美式音色、字幕位置避开 Shorts
界面遮挡、使用粗体拉丁字体，并在批次内轮换你自己的背景音乐。上游默认值针对中文内容，
用于英文文案时效果不佳。

**磁盘管理** —— `--delete-after-upload` 在 YouTube 确认上传后删除本地任务文件；
按每条视频约 65 MB 计算，长期运行时这一点很关键。

以上均为增量改动：未修改上游任何行为，原有命令的使用方式完全不变。
新增代码的测试见 `test/services/test_batch*.py` 与 `test_youtube*.py`。

---

## 界面预览 🖥️

<h4 align="center">WebUI</h4>

![](docs/webui.jpg)

<h4 align="center">API</h4>

![](docs/api.jpg)

---

## 特别感谢 ❤️

<div align="center">
  <a href="https://platform.kimi.com?track_id=track-2f5441d6ffd84c509dd079d78e9db5dc&aff=moneyprinterturbo" target="_blank"><img src="https://gcdn.moonshot.cn/growth-cdn/sponsor/kimi-zh.png" alt="Kimi 赞助 MoneyPrinterTurbo" width="100%"></a>
</div>

感谢 [Kimi](https://platform.kimi.com?track_id=track-2f5441d6ffd84c509dd079d78e9db5dc&aff=moneyprinterturbo) 赞助本项目！[Kimi K3](https://www.kimi.com/blog/kimi-k3?aff=moneyprinterturbo) 是 Moonshot AI 迄今能力最强的模型，也是全球首个开源 3T 级模型，拥有原生视觉能力与 100 万 Token 上下文，在知识工作、推理和长周期任务中展现前沿性能。在 MoneyPrinterTurbo 中，K3 能直接驱动视频创作，不仅撰写视频文案，还会提炼素材搜索关键词、决定成片画面；对内容理解越准确，匹配到的素材就越贴题。

**MoneyPrinterTurbo 用户专属优惠：新用户通过专属链接注册，首次成功充值可获充值金额 10% 的 API 额度，最高赠送 ¥1000。活动截至 2026 年 9 月 30 日。前往 Kimi 开放平台（[中文站](https://platform.kimi.com?track_id=track-2f5441d6ffd84c509dd079d78e9db5dc&aff=moneyprinterturbo)｜[Global](https://platform.kimi.ai?track_id=track-f6b0a640d35c41deb03b247242a1058c&aff=moneyprinterturbo)）体验 API。**
<br>

<table align="center">
  <tr>
    <td align="center" width="120">
      <a href="https://www.volcengine.com/activity/ai618?utm_campaign=hw&utm_content=hw&utm_medium=devrel_tool_web&utm_source=OWO&utm_term=MoneyPrinterTurbo"><img src="docs/sponsors/volcengine-logo.svg" alt="火山引擎" height="32"></a><br>
      <a href="https://www.volcengine.com/activity/ai618?utm_campaign=hw&utm_content=hw&utm_medium=devrel_tool_web&utm_source=OWO&utm_term=MoneyPrinterTurbo"><strong>火山引擎</strong></a>
    </td>
    <td align="left">
      感谢字节火山引擎赞助本项目！ <strong>【专属活动优惠】</strong>19元Tokens包！享字节自研豆包模型+满血版开源 SOTA模型，覆盖文本、VLM、图像生成，全模态一站配齐：Seed-2.1、Seedream-5.0、GLM-5.2、DeepSeek、Qwen等。不止编程，更能解决 Agent 复杂长程任务 --&gt; <a href="https://www.volcengine.com/activity/ai618?utm_campaign=hw&utm_content=hw&utm_medium=devrel_tool_web&utm_source=OWO&utm_term=MoneyPrinterTurbo">注册即领2500万Tokens，立即前往</a>
    </td>
  </tr>
  <tr>
    <td align="center" width="120">
      <a href="https://www.ccsub.net/register?ref=VCVDAWWY"><img src="docs/sponsors/ccsub-logo.png" alt="CCSub" height="36"></a><br>
      <a href="https://www.ccsub.net/register?ref=VCVDAWWY"><strong>CCSub</strong></a>
    </td>
    <td align="left">
      感谢 <a href="https://www.ccsub.net/register?ref=VCVDAWWY">CCSub</a> 赞助本项目！<strong>CCSub 是稳定、实惠的 AI API 中转平台，是 Claude Code 官方订阅的超强平替。</strong>一个 API Key 即可调用 Claude Opus 4.8、Sonnet 4.6、Haiku 4.5、GPT-5、Gemini 等模型，价格约为官方直连的 1/3，全球直连无需梯子。兼容 Claude Code、Codex、Cursor、Cline、Continue、Windsurf 等所有主流 AI 编程工具。前往 <a href="https://www.ccsub.net/register?ref=VCVDAWWY">www.ccsub.net</a> 注册即送 $5 体验额度。
    </td>
  </tr>
  <tr>
    <td align="center" width="120">
      <a href="https://infistar.ai/register?aff=6T4EYXP2&amp;ref_source=link"><img src="docs/sponsors/infistar-logo.svg" alt="Infistar.ai 无限星河" height="56"></a><br>
      <a href="https://infistar.ai/register?aff=6T4EYXP2&amp;ref_source=link"><strong>Infistar.ai 无限星河</strong></a>
    </td>
    <td align="left">
      感谢 <a href="https://infistar.ai/register?aff=6T4EYXP2&amp;ref_source=link">Infistar.ai 无限星河</a> 赞助本项目！<br>
      ⚡ 超低成本与稳定调度：价格低至官方 1 折，模型倍率与调用明细全程透明；多路供应动态调度，告别限流与断连困扰。<br>
      🧠 全系大模型完美驱动脚本：全面覆盖 OpenAI、Claude、Google Gemini、DeepSeek、通义千问（Qwen）等主流 LLM，兼容 OpenAI 标准接口，为 MoneyPrinterTurbo 的文案生成与素材关键词提炼提供低延迟、高并发支持。<br>
      🎨 前沿多模态生态：全面接入 FLUX、Midjourney、Seedance、可灵（Kling）、Sora、Luma 等顶级生图与视频模型，满足下一代 AI 视频生成需求。<br>
      🎁 MoneyPrinterTurbo 用户专属福利：通过 <a href="https://infistar.ai/register?aff=6T4EYXP2&amp;ref_source=link">专属推广链接</a> 注册即享 [专属赠送额度 / 首充特惠]，开箱即用！
    </td>
  </tr>
  <tr>
    <td align="center" width="120">
      <a href="https://www.shengsuanyun.com/?from=CH_XUQ4OTSK"><img src="docs/sponsors/shengsuanyun-logo.jpg" alt="胜算云" height="56"></a><br>
      <a href="https://www.shengsuanyun.com/?from=CH_XUQ4OTSK"><strong>胜算云</strong></a>
    </td>
    <td align="left">
      感谢<a href="https://www.shengsuanyun.com/?from=CH_XUQ4OTSK">胜算云</a>对本项目的赞助！胜算云是面向 AI 原生团队的模型 API 聚合平台，汇集 Claude、ChatGPT、Gemini 等海内外大语言模型及多媒体模型，支持统一接入与按量调用。<br>
      平台坚持合规 API 服务，杜绝逆向工程和资源稀释。此外平台提供企业级定制网关，包括团队成本与权限管理、智能路由、安全防护及 BYOK 密钥托管，并提供发票服务。<br>
      🎁新用户通过<a href="https://www.shengsuanyun.com/?from=CH_XUQ4OTSK">此链接</a>注册，即可领取 10 元 Token 体验额度。
    </td>
  </tr>
  <tr>
    <td align="center" width="120">
      <a href="https://reccloud.cn"><img src="docs/sponsors/reccloud-logo.svg" alt="录咖" height="36"></a><br>
      <a href="https://reccloud.cn"><strong>录咖 AI</strong></a>
    </td>
    <td align="left">
      由于该项目的 <strong>部署</strong> 和 <strong>使用</strong>，对于一些小白用户来说，还是 <strong>有一定的门槛</strong>，在此特别感谢 <a href="https://reccloud.cn">录咖（AI智能 多媒体服务平台）</a> 网站基于该项目，提供的免费 <code>AI视频生成器</code> 服务，可以不用部署，直接在线使用，非常方便。
    </td>
  </tr>
  <tr>
    <td align="center" width="120">
      <a href="https://picwish.cn"><img src="docs/sponsors/picwish-logo.svg" alt="佐糖" height="36"></a><br>
      <a href="https://picwish.cn"><strong>佐糖</strong></a>
    </td>
    <td align="left">
      感谢 <a href="https://picwish.cn">佐糖</a> 对该项目的支持和赞助，使得该项目能够持续的更新和维护。佐糖专注于<strong>图像处理领域</strong>，提供丰富的<strong>图像处理工具</strong>，将复杂操作极致简化，真正实现让图像处理更简单。
    </td>
  </tr>
</table>

## 作者的另一个开源项目：MangoDisk ⭐

<p align="center">
  <a href="https://github.com/harry0703/MangoDisk">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://assets.mangodisk.app/images/readme/zh-dark.jpg">
      <source media="(prefers-color-scheme: light)" srcset="https://assets.mangodisk.app/images/readme/zh-light.jpg">
      <img src="https://assets.mangodisk.app/images/readme/zh-light.jpg" width="900" alt="MangoDisk 开源磁盘清理与空间分析工具">
    </picture>
  </a>
</p>

<p align="center">
  <strong>适用于 macOS 和 Windows 的安全优先开源磁盘清理与空间分析工具</strong><br>
  查找大文件和重复文件，清理缓存与应用残留，安全释放磁盘空间。
</p>

<p align="center">
  <a href="https://github.com/harry0703/MangoDisk">查看 GitHub 开源项目</a>
</p>

---

## 功能特性 🎯

- [x] 提供 **AI Agent**、**WebUI**、**API** 和 **CLI** 四种使用方式，代码按控制器、服务和模型等职责分层
- [x] 支持 **AI 自动生成视频脚本**，也可以使用自定义脚本
- [x] 支持多种 **高清视频** 尺寸
  - [x] 竖屏 9:16，`1080x1920`
  - [x] 横屏 16:9，`1920x1080`
- [x] 支持 **批量视频生成**，可以一次生成多个视频，然后选择一个最满意的
- [x] 支持 **视频片段时长** 设置，方便调节素材切换频率
- [x] 支持 **多语言视频脚本** 生成
- [x] 支持 **Edge TTS**、**Azure Speech**、**SiliconFlow**、**Google Gemini**、**小米 MiMo**、**ElevenLabs** 和 **Chatterbox** 语音合成，可实时试听
- [x] 支持 **字幕生成**，可调整字体、位置、颜色、大小、描边和背景样式
- [x] 支持 **背景音乐**，可随机选择或使用指定音乐，并调整音量
- [x] 支持使用自己的 **本地素材**，也可从 **Pexels**、**Pixabay** 和 **Coverr** 获取可免费使用的高清素材
- [x] 支持 **Kimi / Moonshot AI**、**OpenAI**、**Google Gemini**、**DeepSeek**、**阿里云通义千问**、**Microsoft Azure OpenAI**、**火山引擎方舟**、**xAI Grok**、**MiniMax**、**小米 MiMo** 等主流模型服务，并兼容 **Cloudflare AI Gateway**、**魔搭 ModelScope**、**AIHubMix**、**AIML API**、**EvoLink**、**Ollama**、**OneAPI**、**LiteLLM**、**Groq**、**Pollinations AI** 等统一网关、聚合平台和本地运行环境
- [x] 支持一键 **跨平台发布**，生成完成后可自动上传至 **TikTok**、**Instagram** 和 **YouTube Shorts**

## 作品展示 🎬

以下示例均由 MoneyPrinterTurbo 实际生成。

### 竖屏 9:16

<table width="100%">
<tr>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=03-zh-portrait-city-morning.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/03-zh-portrait-city-morning.jpg" width="180" alt="城市醒来的时刻"></a><br><strong>城市醒来的时刻</strong><br>中文 · 14 秒</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=05-zh-portrait-clean-energy.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/05-zh-portrait-clean-energy.jpg" width="180" alt="清洁能源的未来"></a><br><strong>清洁能源的未来</strong><br>中文 · 24 秒</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=07-zh-portrait-space-exploration.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/07-zh-portrait-space-exploration.jpg" width="180" alt="为什么我们仍要探索太空"></a><br><strong>为什么我们仍要探索太空</strong><br>中文 · 27 秒</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=17-zh-portrait-seed-journey.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/17-zh-portrait-seed-journey.jpg" width="180" alt="一粒种子的旅程"></a><br><strong>一粒种子的旅程</strong><br>中文 · 44 秒</td>
</tr>
<tr>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=09-en-portrait-future-robotics.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/09-en-portrait-future-robotics.jpg" width="180" alt="The Future of Everyday Robotics"></a><br><strong>The Future of Everyday Robotics</strong><br>English · 21 sec</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=11-en-portrait-small-habits.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/11-en-portrait-small-habits.jpg" width="180" alt="Small Habits, Lasting Change"></a><br><strong>Small Habits, Lasting Change</strong><br>English · 19 sec</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=13-en-portrait-creative-work.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/13-en-portrait-creative-work.jpg" width="180" alt="Making Space for Creative Work"></a><br><strong>Making Space for Creative Work</strong><br>English · 20 sec</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=15-en-portrait-coffee-science.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/15-en-portrait-coffee-science.jpg" width="180" alt="The Science Inside Coffee"></a><br><strong>The Science Inside Coffee</strong><br>English · 23 sec</td>
</tr>
</table>

### 横屏 16:9

<table width="100%">
<tr>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=02-zh-landscape-deep-ocean.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/02-zh-landscape-deep-ocean.jpg" width="280" alt="深海里的微光"></a><br><strong>深海里的微光</strong><br>中文 · 23 秒</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=04-zh-landscape-reading-power.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/04-zh-landscape-reading-power.jpg" width="280" alt="阅读如何塑造我们"></a><br><strong>阅读如何塑造我们</strong><br>中文 · 23 秒</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=06-zh-landscape-pour-over-coffee.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/06-zh-landscape-pour-over-coffee.jpg" width="280" alt="一杯手冲咖啡的细节"></a><br><strong>一杯手冲咖啡的细节</strong><br>中文 · 23 秒</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=08-zh-landscape-spring-journey.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/08-zh-landscape-spring-journey.jpg" width="280" alt="春天适合出发"></a><br><strong>春天适合出发</strong><br>中文 · 14 秒</td>
</tr>
<tr>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=10-en-landscape-ocean-conservation.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/10-en-landscape-ocean-conservation.jpg" width="280" alt="Why Ocean Conservation Matters"></a><br><strong>Why Ocean Conservation Matters</strong><br>English · 25 sec</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=14-en-landscape-sustainable-cities.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/14-en-landscape-sustainable-cities.jpg" width="280" alt="Designing More Sustainable Cities"></a><br><strong>Designing More Sustainable Cities</strong><br>English · 27 sec</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=16-en-landscape-mountain-perspective.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/16-en-landscape-mountain-perspective.jpg" width="280" alt="What Mountains Teach Us"></a><br><strong>What Mountains Teach Us</strong><br>English · 18 sec</td>
<td align="center" width="25%"><a href="https://harry0703.github.io/mpt-assets/?video=18-en-landscape-history-of-flight.mp4"><img src="https://github.com/harry0703/mpt-assets/releases/download/assets/18-en-landscape-history-of-flight.jpg" width="280" alt="A Brief History of Human Flight"></a><br><strong>A Brief History of Human Flight</strong><br>English · 59 sec</td>
</tr>
</table>

## 配置要求 📦

- 建议系统：Windows 10、macOS 11.0 或更高版本，以及主流 Linux 发行版
- 本地部署需要 Python 3.11 或更高版本，推荐使用 Python 3.11
- GPU 不是必需项，但如果你希望本地转录、更快的视频处理或更顺畅的批量生成体验，建议使用带显存的独立显卡

| 项目 | 最低配置 | 推荐配置        | 理想配置        |
| ---- | -------- | --------------- | --------------- |
| CPU  | 4 核     | 6 到 8 核       | 8 核及以上      |
| RAM  | 4 GB     | 8 GB            | 16 GB 及以上    |
| GPU  | 非必须   | 4 GB 显存及以上 | 8 GB 显存及以上 |

- 如果你主要依赖云端 LLM、云端 TTS 和在线素材源，CPU 与内存比 GPU 更重要
- 如果你启用 `faster-whisper`、批量生成或更重的本地处理链路，GPU 会明显提升速度

## 快速开始 🚀

### 推荐使用方式

- 不想手动安装和配置：直接使用 AI Agent 生成视频
- Windows 用户：优先使用一键启动包，适合快速体验
- macOS / Linux 用户：优先使用 `uv` 进行本地部署
- 想要隔离运行环境：优先使用 Docker 部署

### 使用 AI Agent 生成视频

如果你的 AI Agent 支持读取 Skill 文档并操作本地终端，可以直接发送下面这段话。Agent 会自动完成安装、配置和视频生成；只有缺少必要的 API Key 时才会向你询问，完成后会返回生成的视频文件路径。目前支持 macOS 和 Windows。

```text
使用这个 Skill：https://raw.githubusercontent.com/harry0703/MoneyPrinterTurbo/main/docs/skill/SKILL.md
帮我生成一个主题为“人工智能如何改变普通人的日常生活”的视频。
```

### 在 Google Colab 中运行

免去本地环境配置，点击直接在 Google Colab 中快速体验 MoneyPrinterTurbo

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/harry0703/MoneyPrinterTurbo/blob/main/docs/MoneyPrinterTurbo.ipynb)

### Windows 一键启动包

下载一键启动包，解压直接使用（路径不要有 **中文**、**特殊字符**、**空格**）

- GitHub Releases：https://github.com/harry0703/MoneyPrinterTurbo/releases/latest

下载后，建议先**双击执行** `update.bat` 更新到**最新代码**，然后双击 `start.bat` 启动

启动后，会自动打开浏览器（如果打开是空白，建议换成 **Chrome** 或者 **Edge** 打开）

## 安装部署 📥

### 前提条件

- 本地部署需要 Python 3.11 或更高版本
- Windows 用户建议避免使用包含中文、特殊字符或空格的项目路径

#### ① 克隆代码

```shell
git clone https://github.com/harry0703/MoneyPrinterTurbo.git
```

#### ② 配置项目（可选）

首次启动时，项目会根据 `config.example.toml` 自动创建 `config.toml`。大模型 Provider、素材来源和相关 API Key 可以直接在 WebUI 的基础设置中配置。

### Docker 部署 🐳

#### ① 启动 Docker

如果未安装 Docker，请先安装 https://www.docker.com/products/docker-desktop/

Windows 用户可以参考微软的文档：

1. https://learn.microsoft.com/zh-cn/windows/wsl/install
2. https://learn.microsoft.com/zh-cn/windows/wsl/tutorials/wsl-containers

```shell
cd MoneyPrinterTurbo
docker compose -f docker-compose.release.yml up
```

> 默认推荐使用 `docker-compose.release.yml`，它会直接拉取 GitHub Container Registry 上的预构建镜像：`ghcr.io/harry0703/moneyprinterturbo:latest`。
> 如果你需要本地重新构建镜像，可以继续使用 `docker compose up`。
> 首次启动前，请将 `config.example.toml` 复制为 `config.toml`，供容器挂载使用。

#### ② 访问 WebUI

打开浏览器，访问 http://127.0.0.1:8501

#### ③ 访问 API 文档

打开浏览器，访问 http://127.0.0.1:8080/docs 或者 http://127.0.0.1:8080/redoc

### 手动部署 📦

> 视频教程

- 完整的使用演示：https://v.douyin.com/iFhnwsKY/
- 如何在 Windows 上部署：https://v.douyin.com/iFyjoW3M

#### ① 创建虚拟环境

推荐使用 [uv](https://docs.astral.sh/uv/) 管理 Python 环境和依赖。项目支持 Python 3.11 或更高版本，以下示例使用 Python 3.11。

```shell
git clone https://github.com/harry0703/MoneyPrinterTurbo.git
cd MoneyPrinterTurbo
uv python install 3.11
uv sync --frozen
```

如果你暂时不使用 `uv`，也可以继续使用 `venv + pip`

```shell
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

说明：

- `pyproject.toml` 是主依赖定义文件
- `uv.lock` 是锁文件，建议默认执行 `uv sync --frozen`
- `requirements.txt` 仅保留给旧的 `pip` 安装方式兼容使用

#### ② 启动 WebUI 🌐

注意需要到 MoneyPrinterTurbo 项目 `根目录` 下执行以下命令

###### Windows

```powershell
.\webui.bat
```

在 CMD 中也可以执行 `webui.bat`。
`webui.bat` 会优先使用项目 `.venv` 或一键包内置 Python；如果没有找到项目 Python，但已安装 `uv`，会自动切换为 `uv run streamlit`。
如需允许局域网内其他设备访问 WebUI，可以先执行 `set MPT_WEBUI_HOST=0.0.0.0`，再运行 `webui.bat`。

###### macOS 或 Linux

```shell
sh webui.sh
```

脚本会自动使用项目虚拟环境或 `uv`，并选择可用的本地端口。如需允许局域网内其他设备访问，可以执行：

```shell
MPT_WEBUI_HOST=0.0.0.0 sh webui.sh
```

启动后，会自动打开浏览器（如果打开是空白，建议换成 **Chrome** 或者 **Edge** 打开）

#### ③ 启动 API 服务 🚀

```shell
uv run python main.py
```

如果你已经手动激活了虚拟环境，也可以直接执行：

```shell
python main.py
```

#### ④ 纯命令行方式（无浏览器）⌨️

如果你无法使用浏览器或端口转发，可以直接在命令行生成视频。最简单的完整视频生成命令如下：

```shell
uv run python cli.py --video-subject "人工智能如何改变日常生活"
```

如需查看完整命令、参数说明和使用方法，可以执行：

```shell
uv run python cli.py --help
```

#### ⑤ 批量生成与 YouTube 发布 📺

从主题清单批量生成视频，每个主题一条。把主题逐行写入文本文件（空行和以 `#` 开头的行会被忽略）：

```shell
uv run python cli.py --subjects-file topics.txt --batch-output-dir ./out
```

程序会先为所有主题生成文案并写入批次清单，然后逐条渲染视频。清单在每一步之后都会保存，因此中断后可以用同一个文件续跑：已完成的主题会被跳过，失败的主题会重试。

```shell
uv run python cli.py --batch-manifest storage/batches/<batch-id>/manifest.json
```

单个主题失败只会被记录下来，批次会继续处理后续主题。使用 `--stop-at script` 可以先生成全部文案，检查或修改清单之后再渲染。

项目默认音色为中文，用它朗读英文文案会带有明显口音。制作英文内容时，可以为每个主题随机选用一个美式英语音色：

```shell
uv run python cli.py --subjects-file topics.txt --random-voice en-US
```

需要 Azure V2 密钥的音色会被自动排除；每个主题实际使用的音色会记录在清单中，续跑时保持一致。

如果需要用你自己的 Google Cloud 项目把成片上传到 YouTube：

1. 在 Google Cloud 控制台启用 **YouTube Data API v3**。
2. 创建 **Desktop app** 类型的 OAuth 客户端，把下载的 JSON 保存为
   `storage/youtube_client_secret.json`。
3. 安装可选依赖：`uv sync --extra youtube`
4. 完成一次授权：`uv run python cli.py --youtube-auth`
5. 在 `config.toml` 中设置 `youtube_upload_enabled = true`，或使用 `--publish`。

```shell
uv run python cli.py --subjects-file topics.txt --publish
```

> **注意：** 未通过 Google 审核的 API 项目，通过接口上传的视频会被强制设为**私享**，
> `--youtube-privacy` 的设置不会生效，命令在检测到这种情况时会给出警告。
> 你可以在 Google Cloud 控制台申请审核，或在此之前先在 YouTube Studio 手动公开视频。

批量运行会持续占用磁盘：每个视频在 `storage/tasks/` 下大约占 65 MB。使用
`--delete-after-upload` 可以在 YouTube 确认上传成功后删除对应的任务目录：

```shell
uv run python cli.py --subjects-file topics.txt --publish --delete-after-upload
```

只有确实拿到 video id 才会删除，上传失败或被跳过时本地文件一定保留；未开启发布时该选项会被忽略。

#### ⑥ 批量界面 🖥️

用于启动和监控批量任务的独立浏览器界面，与主 WebUI 相互独立，可以同时运行：

```shell
sh batch-ui.sh
```

界面地址为 http://127.0.0.1:8601 ，可以直接输入主题列表、选择音乐目录并启动任务。
批量任务运行在独立进程中，刷新页面或关闭标签页都不会中断渲染。面板会显示每个主题的
状态、分配到的背景音乐、生成的文案、实时日志、成片以及 YouTube 链接。

## 语音合成 🗣

默认使用免费的 **Edge TTS**，在 WebUI 中显示为 **Azure TTS V1**。项目同时支持 **Azure TTS V2**、**SiliconFlow TTS**、**Google Gemini TTS**、**小米 MiMo TTS**、**ElevenLabs TTS**、自托管 **Chatterbox TTS**，以及无配音模式。

可直接在 WebUI 中选择 Provider 和音色，并按照界面提示填写所需凭据。Edge TTS 不需要 API Key；[Azure TTS V2](https://portal.azure.com/) 及其他云端服务需要对应平台的凭据。Edge TTS 音色可查看：[音色列表](./docs/voice-list.txt)。

## 字幕生成 📜

当前支持两种字幕生成方式：

- **edge**：使用 TTS 时间戳生成字幕，速度快，不需要 GPU，默认使用该模式。
- **whisper**：使用本地 `faster-whisper` 转写音频，适用于需要更准确字幕时间轴的场景。首次使用时需要下载模型。

在 `config.toml` 中修改 `subtitle_provider` 即可切换模式。Whisper 默认使用约 3 GB 的 `large-v3`；如需更小、更快的模型，可以使用约 1.6 GB 的 `large-v3-turbo`：

```toml
[app]
subtitle_provider = "whisper"

[whisper]
model_size = "large-v3-turbo"
```

> 首次使用 Whisper 时，程序会自动从 Hugging Face 下载模型。如果当前网络无法自动下载，可以从 [Hugging Face](https://huggingface.co/Systran/faster-whisper-large-v3) 手动下载 `whisper-large-v3`。

下载并解压后，将整个目录放到 `.\MoneyPrinterTurbo\models`，最终路径应为 `.\MoneyPrinterTurbo\models\whisper-large-v3`：

```
MoneyPrinterTurbo
  ├─models
  │   └─whisper-large-v3
  │          config.json
  │          model.bin
  │          preprocessor_config.json
  │          tokenizer.json
  │          vocabulary.json
```

## 背景音乐 🎵

用于视频的背景音乐，位于项目的 `resource/songs` 目录下。

> 当前项目里面放了一些默认的音乐，来自于 YouTube 视频，如有侵权，请删除。

## 字幕字体 🅰

用于视频字幕的渲染，位于项目的 `resource/fonts` 目录下，你也可以放进去自己的字体。

## 常见问题 🤔

<details>
<summary>如何发布到 TikTok、Instagram 或 YouTube Shorts？</summary>

注册 [Upload-Post](https://upload-post.com/) 账号并获取 API Key，然后在 `config.toml` 的 `[app]` 下添加以下配置：

```toml
[app]
upload_post_enabled = true
upload_post_api_key = "your-api-key"
upload_post_username = "your-username"
upload_post_platforms = ["tiktok", "instagram", "youtube"]
upload_post_auto_upload = true
upload_post_youtube_privacy_status = "public"
```

保存配置并重启项目。视频生成完成后，程序会自动发布到已配置的平台。YouTube 可见性可设置为 `public`、`unlisted` 或 `private`。

</details>

<details>
<summary>RuntimeError: No ffmpeg exe could be found</summary>

通常情况下，ffmpeg 会被自动下载，并且会被自动检测到。
但是如果你的环境有问题，无法自动下载，可能会遇到如下错误：

```
RuntimeError: No ffmpeg exe could be found.
Install ffmpeg on your system, or set the IMAGEIO_FFMPEG_EXE environment variable.
```

此时你可以从 https://www.gyan.dev/ffmpeg/builds/ 下载ffmpeg，解压后，设置 `ffmpeg_path` 为你的实际安装路径即可。

```toml
[app]
# 请根据你的实际路径设置，注意 Windows 路径分隔符为 \\
ffmpeg_path = "C:\\Users\\harry\\Downloads\\ffmpeg.exe"
```

</details>

<details>
<summary>OSError: [Errno 24] Too many open files</summary>

这个问题是由于系统打开文件数限制导致的，可以通过修改系统的文件打开数限制来解决。

查看当前限制

```shell
ulimit -n
```

如果过低，可以调高一些，比如

```shell
ulimit -n 10240
```

</details>

<details>
<summary>Whisper 模型下载失败</summary>

```
LocalEntryNotFoundError: Cannot find an appropriate cached snapshot folder for the specified revision on the local disk and
outgoing traffic has been disabled.
To enable repo look-ups and downloads online, pass 'local_files_only=False' as input.
```

或者

```
An error occurred while synchronizing the model Systran/faster-whisper-large-v3 from the Hugging Face Hub:
An error happened while trying to locate the files on the Hub and we cannot find the appropriate snapshot folder for the
specified revision on the local disk. Please check your internet connection and try again.
Trying to load the model directly from the local cache, if it exists.
```

解决方法：[查看如何从 Hugging Face 手动下载模型](#%E5%AD%97%E5%B9%95%E7%94%9F%E6%88%90-)

</details>

## 反馈建议 📢

- 可以提交 [issue](https://github.com/harry0703/MoneyPrinterTurbo/issues) 或者 [pull request](https://github.com/harry0703/MoneyPrinterTurbo/pulls)。

## 许可证 📝

点击查看 [`LICENSE`](LICENSE) 文件

## Star History

<a href="https://www.star-history.com/?repos=harry0703%2FMoneyPrinterTurbo&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=harry0703/MoneyPrinterTurbo&type=date&theme=dark&legend=top-left&sealed_token=AtOR8By6GcNKd46eJLixrnucHF_99GOSBBKfc60pAm2xsDylemaYxDMcvTlPRz-G_onzDrs-hDrM0xdKkn0L6PgDin3fv02ViVtsZvgRYgk0YOzkX2KgLG8wro66VGphii-u6GNpzD8JocrqGGKvsFSpmbRqo5g-2mEDaN7-ESdtF48ZH0rDOCpoc1Mh" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=harry0703/MoneyPrinterTurbo&type=date&legend=top-left&sealed_token=AtOR8By6GcNKd46eJLixrnucHF_99GOSBBKfc60pAm2xsDylemaYxDMcvTlPRz-G_onzDrs-hDrM0xdKkn0L6PgDin3fv02ViVtsZvgRYgk0YOzkX2KgLG8wro66VGphii-u6GNpzD8JocrqGGKvsFSpmbRqo5g-2mEDaN7-ESdtF48ZH0rDOCpoc1Mh" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=harry0703/MoneyPrinterTurbo&type=date&legend=top-left&sealed_token=AtOR8By6GcNKd46eJLixrnucHF_99GOSBBKfc60pAm2xsDylemaYxDMcvTlPRz-G_onzDrs-hDrM0xdKkn0L6PgDin3fv02ViVtsZvgRYgk0YOzkX2KgLG8wro66VGphii-u6GNpzD8JocrqGGKvsFSpmbRqo5g-2mEDaN7-ESdtF48ZH0rDOCpoc1Mh" />
 </picture>
</a>
