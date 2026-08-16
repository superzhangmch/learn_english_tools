# custom_mac_etymology_dict

为 macOS Dictionary.app 制作的自定义词典：约 1.3 万英文单词的**中文词源**。屈折/派生形式（`dogs` → dog、`claims` → claim、`legally` → legal、`children` → child、`went` → go、`teacher` → teach 等）通过预先生成的 `<d:index>` 别名都能查到。

> English version: [README.md](README.md)

## 依赖

- macOS（Apple Silicon / Intel 都行）
- Python 3.11+
- Xcode Command Line Tools：`xcode-select --install`
- Python 包：`pip3.11 install lemminflect nltk pyobjc-framework-CoreServices`

## 快速开始

```bash
git clone https://github.com/superzhangmch/custom_mac_etymology_dict.git
cd custom_mac_etymology_dict

# 第三方编译工具（Apple 官方 DDK 不可自由分发，但这个开源版提供同样的二进制）
git clone https://github.com/jjgod/mac-dictionary-kit.git

./build.sh
```

打开 **Dictionary.app → 偏好设置 (⌘,) → 勾选 EtymologyDict**。

测试一下：任意 App 里选中英文单词 → 重击 / ⌃⌘D，应该能看到中文词源弹窗。

## `data/` 里有什么

| 文件 | 格式 | 来源 |
|---|---|---|
| `etymology_dict.json` | `{"word": "中文词源说明", ...}`（1.29 万条） | AI 生成 |
| `word_source.js` | `var word_source = {"word": [ref_cnt, ...], ...};`（2.66 万条；第一个元素是该词在英文词表里的出现次数） | [zyronon/typing-word](https://github.com/zyronon/typing-word) |

## 构建流程

`./build.sh` 三步走：

1. **`build_full.py`** — 读数据文件，生成带所有别名 index 的 AppleDict XML，输出到 `objects/apple_src/EtymologyDict.xml`
2. **`make`** — 调用 `mac-dictionary-kit/ddk/build_dict.sh`，把 XML 编译成带二进制索引的 `.dictionary` bundle
3. **`make install`** — 拷贝到 `~/Library/Dictionaries/`，然后 kill 掉 `LookupViewService` 和 `Dictionary` 刷新缓存

通过环境变量覆盖路径：

```bash
DATA_DIR=~/my_data \
OUTPUT_DIR=./build \
DDK_PATH=./mac-dictionary-kit/ddk \
PYTHON=python3.12 \
./build.sh
```

## 别名是怎么生成的

Apple 内置词典在编译时已经把所有屈折形式作为查询 key 烧进了二进制索引（解压 NOAD 的 `Body.data` 能看到 `dogs`、`running` 都作为 `<d:index>` 写在 `dog`、`run` 的条目里）。**系统"重击"弹窗对自定义词典不做 lemmatization**——只查精确匹配。所以只放 `dog`，查 `dogs` 就找不到。

因此 `build_full.py` 对每个基础词正向生成三层别名：

1. **屈折变化**（`lemminflect`）：
   - `claim` → `claims, claimed, claiming`
   - `run` → `runs, running, ran`
   - `child` → `children`
   - `go` → `goes, going, gone, went`

2. **形容词 → 副词**（拼写规则）：
   - `legal` → `legally`
   - `simple` → `simply`（去 `e` 加 `y`）
   - `happy` → `happily`（`y` → `ily`）
   - `basic` → `basically`（`-ic` → `-ally`）
   - `true` → `truly`（去 `e`）

3. **派生形式**（WordNet）：
   - `teach` → `teacher, teaching, teachable`
   - `create` → `creation, creator, creative, creature`

最终：1.28 万基础词 → 约 7.5 万可查询 key。

## 重要注意事项

### 1. 不要盲目遍历 `synset.lemmas()`

用 WordNet 拿派生形式时，**只能取与查询词同名的 lemma**：

```python
for syn in wn.synsets(key):
    for lemma in syn.lemmas():
        if lemma.name().lower() != key:
            continue                       # <-- 关键
        for related in lemma.derivationally_related_forms():
            ...
```

WordNet 把同义词归到同一个 synset。例如 `work` 和 `ferment` 同属 `ferment.v.03`。如果不过滤，`work` 就会继承 `ferment` 的派生（`fermenting`、`fermentation`），导致 `fermenting` 查出来变成 "work"——这是 **同义** 当成 **派生** 的典型错误。加这一行过滤能去掉约 80% 的假阳性别名。

### 2. 缓存刷新

Dictionary.app 和 `LookupViewService` 都做激进缓存。重装后：

```bash
killall -9 LookupViewService Dictionary
```

不够就注销重登/重启。`build.sh` 已自动执行这一步。

### 3. 用 inline `<span>` 而不是 block `<h1>`/`<p>`

弹窗给每个词典固定高度。块级元素各自换行浪费纵向空间——你的条目只显示一行 + 一个"更多"链接。要全部用 inline `<span>`（看 Apple 自带词典就知道），让内容像段落一样自然换行，能显示 3–4 行。

### 4. 屈折形式系统不会自动 lemma 化

Apple 内置词典之所以 `dogs` 能查到，是因为 NOAD 的 `dog` 条目里写了 `<d:index d:value="dogs"/>`。macOS 不会替你做形态分析——它只查精确字符串。我们用预生成别名来复制这个机制。

## 卸载

```bash
rm -rf ~/Library/Dictionaries/EtymologyDict.dictionary
killall LookupViewService Dictionary
```

## 文件结构

```
.
├── build.sh             # 一键构建 + 安装 + 刷新
├── build_full.py        # 生成带别名的 AppleDict XML
├── data/                # 词源 + 词频数据
├── README.md            # 英文版
└── README_zh.md         # 中文版（本文件）
```

## 致谢

- [jjgod/mac-dictionary-kit](https://github.com/jjgod/mac-dictionary-kit) —— Apple Dictionary Development Kit 的开源版本
- [zyronon/typing-word](https://github.com/zyronon/typing-word) —— 词频数据
- [lemminflect](https://github.com/bjascob/LemmInflect)、[NLTK WordNet](https://www.nltk.org/howto/wordnet.html) —— 形态学工具
