# learning_english

三个自用的英语学习小工具，共用同一套「选中文字 → LLM 解读」的交互。

| 子目录 | 是什么 | 状态 |
|---|---|---|
| `news_reader/` | 英文新闻阅读器：e-ink 时钟仪表盘 / 手机版新闻列表 / 阅读模式 | 已并入 |
| `book_reader/` | epub 阅读器 | 待其维护者并入 |
| `audio_player/` | 音频播放器 + 字幕转写 | 待其维护者并入 |

每个子目录是一个**自包含**的项目：有自己的 `server.py`、`requirements.txt`、
`config_example.py`，各自独立运行，互不导入。合库只是为了放在一起管，不是要做成
一个大工程。

## 约定

- **真实配置不进库。** 每个子项目的 `config.py`（含 API key）都被根 `.gitignore`
  忽略——那些 pattern 不带前导斜杠，所以对任意层级都生效，`news_reader/config.py`
  和 `book_reader/config.py` 一样被挡住。照各自的 `config_example.py` 建即可。
- **路径一律相对于模块文件自身**（`Path(__file__).parent` / `HERE`），不要引用仓库根。
  正因如此，整个子项目目录可以随便挪位置而不用改代码。

## 已知重复

`interpret-widget.js`（选中弹窗 + 右侧解读抽屉 + 追问）在三个项目里**各存了一份**，
`/api/interpret` 那套 prompt 也是各自一份拷贝。目前刻意没有强行抽公共库——三个项目
的语境不同（新闻 / 书 / 字幕），prompt 措辞需要分别调。但要清楚：**修了一处 bug 记得
看看另两处有没有同样的毛病**。已经发生过两次跨项目的同源 bug：

- prompt 里「常见词可省略读音」和「绝不省略音标」自相矛盾（book_reader 先发现）
- `max_tokens` 是**输出**额度，推理模型会把它花在 `reasoning_content` 上，若转发代码
  只取 `delta.content`，思考一长正文就是空的

## 并入方式（给另外两位维护者）

`audio_player` 原本是独立仓库 `superzhangmch/local_player`，有 17 个 commit，**要保留
历史**，所以用 subtree 而不是直接拷文件：

```bash
git remote add local_player git@github.com:superzhangmch/local_player.git
git subtree add --prefix=audio_player local_player main
```

`book_reader` 原本不是 git 仓库，没有历史要保，直接把源码放进 `book_reader/` 即可
（注意别把 `.venv/`、`cache/`、`reader.db`、`config.py` 带进来）。

## news_reader 的部署

线上跑在 x13 本机（tailscale-only），systemd 用户级服务 `news-reader.service`，
线上目录 `~/apps/news-reader/`。改完从 `news_reader/` 拷过去再重启：

```bash
rsync -a --exclude config.py news_reader/ ~/apps/news-reader/
systemctl --user restart news-reader.service
curl -s http://100.80.14.27:8399/api/models     # 只监听 tailscale IP, 别用 localhost
```

改了 `static/interpret-widget.js` 记得把三个页面里的 `?v=YYYYMMDDx` 版本号 bump 一下。
