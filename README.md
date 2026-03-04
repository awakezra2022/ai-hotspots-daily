# AI 信息汇总

每日抓取中文科技媒体 AI 热点，默认输出 10 条，并同步更新网站数据。

## 已接入来源

- 36氪：`https://36kr.com/feed`
- 爱范儿：`https://www.ifanr.com/feed`
- 雷峰网：`https://www.leiphone.com/feed`
- IT之家：`https://www.ithome.com/rss/`
- 虎嗅：`https://www.huxiu.com/rss/0.xml`

## 运行方式

```bash
python3 scripts/fetch_ai_hotspots.py --limit 10
```

常用参数：

- `--limit`：输出条数（默认 `10`）
- `--days`：仅保留最近 N 天内容（默认 `3`）
- `--output-dir`：输出目录（默认 `output`）
- `--site-dir`：网站目录（默认 `site`，会写入 `site/data/latest.json`）

## 输出文件

- 每日文件：`output/ai_hotspots_YYYY-MM-DD.md`
- 最新快照：`output/latest_ai_hotspots.md`
- 网站数据：`site/data/latest.json`

## 本地打开网站

先生成数据：

```bash
python3 scripts/fetch_ai_hotspots.py --limit 10
```

再启动静态服务：

```bash
python3 -m http.server 8000 --directory site
```

浏览器访问：`http://127.0.0.1:8000`

## 定时执行（可选）

每天早上 09:00 自动执行：

```bash
crontab -l 2>/dev/null; echo "0 9 * * * cd /Users/mouruijun/Documents/codex/AI信息汇总 && /usr/bin/python3 scripts/fetch_ai_hotspots.py --limit 10 >> output/cron.log 2>&1" | crontab -
```

## 部署到 GitHub Pages（免费）

项目已内置工作流：`.github/workflows/deploy-pages.yml`

- 每天北京时间 09:00 自动更新（GitHub 使用 UTC，配置为 `0 1 * * *`）
- 支持手动触发部署（Actions 页面点击 `Run workflow`）

### 1) 初始化并推送到 GitHub 仓库

如果这个目录还不是独立 Git 仓库，可执行：

```bash
cd /Users/mouruijun/Documents/codex/AI信息汇总
git init
git add .
git commit -m "feat: AI 热点站点与自动发布"
git branch -M main
git remote add origin <你的仓库地址>
git push -u origin main
```

### 2) 在 GitHub 开启 Pages

1. 打开仓库 `Settings -> Pages`
2. `Build and deployment` 的 `Source` 选择 `GitHub Actions`
3. 进入 `Actions`，运行 `Deploy AI Hotspots Site`（或等待每天自动执行）

### 3) 访问地址

部署成功后，访问：

`https://<你的GitHub用户名>.github.io/<仓库名>/`
