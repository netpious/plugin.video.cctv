# plugin.video.cctv

**在线浏览央视网（cctv.com）中的电视节目和文章。** 

<p align="center"><img src="plugin.video.cctv/icon.png" width="160" alt="央视网插件图标"></p>

## 功能

| 入口    | 内容                                                                              |
| ------- | --------------------------------------------------------------------------------- |
| 首页    | 按央视网首页的板块编排：今日热门、直播导视、片库、看点、收视 TOP 榜、经典影视剧场 |
| 新闻    | 要闻、国内、国际、社会、法治、科技、文娱、教育、健康九个子栏目，可阅读全文        |
| 视频    | 央视网视频库的最新发布                                                            |
| 体育    | 19 个赛事频道 + 15 个央视名栏，按最新发布列出视频与图文                           |
| 赛事    | 央视体育赛程里的比赛视频，附每场的技术统计文本                                    |
| 4K 专区 | 超高清专区                                                                        |
| 栏目    | 348 个电视栏目，先按 CCTV 频道分组，再逐期回看                                    |
| 直播    | CCTV 各频道在线直播（不可用）                                                     |
| 搜索    | 关键词检索                                                                        |

## 画质

| 线路                                               | 内容                 | 结果   |
| -------------------------------------------------- | -------------------- | ------ |
| `/asp/hls/{档位}/…`                                | 明文 TS + H.264      | 正常   |
| `/asp/enc/hls/`、`/asp/enc2/hls/`、`/asp/h5e/hls/` | 同一套转码梯的加密版 | 不可用 |

| 内容                         | 最高档          |
| ---------------------------- | --------------- |
| 4K 专区（16 部）             | **1920×1080**   |
| 新闻联播                     | 1280×720        |
| 视频库竖屏短片               | 720×1280        |
| 今日说法、动画大放映等老栏目 | 640×360         |
| 赛事集锦 / 比赛回顾          | 640×360 480×270 |

## 声明

本项目是第三方客插件，与央视网、中央广播电视总台无任何关联，也未获其授权。它只解析央视网公开页面中本就提供给浏览器的内容，不提供、不托管、不修改任何受版权保护的媒体。

所有内容的版权归原权利人所有；使用本项目产生的一切后果由使用者自负，请遵守央视网服务条款与所在地法律。

## 许可证

[MIT](LICENSE) © 2026 netpious ｜ 作者：netpious

---

**English** — A third-party Kodi addon that browses public cctv.com pages (home, news, videos, sports, match highlights, 4K zone, TV columns, live channels and search) inside Kodi's native interface, with long-press preview and download. It ships no media, does not circumvent DRM, and is not affiliated with CCTV. Licensed under MIT.
