# A股量化分析平台 V2

三大功能：全市场资金选股 / 单股资金分析 / 新闻资讯AI深度分析

## 本地运行

```bash
pip install -r requirements.txt
python app.py
```
访问 http://localhost:5000

---

## 部署到 Render（免费）

### 第一步：上传到 GitHub

1. 在 GitHub 新建仓库（Public 或 Private 均可）
2. 把本项目文件夹上传：
```bash
git init
git add .
git commit -m "initial"
git branch -M main
git remote add origin https://github.com/你的用户名/仓库名.git
git push -u origin main
```

### 第二步：在 Render 部署

1. 访问 https://render.com，注册/登录
2. 点击 **New → Web Service**
3. 连接你的 GitHub 仓库
4. 配置如下：

| 字段 | 填写内容 |
|------|---------|
| Name | astock-platform（随意） |
| Region | Singapore（延迟最低） |
| Branch | main |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 600` |
| Instance Type | Free |

5. 点击 **Create Web Service**，等待部署完成（约3-5分钟）

### 注意事项

- **免费实例会休眠**：15分钟无访问后休眠，第一次访问需等20-30秒唤醒
- **内存限制**：免费版512MB，够用但不要同时分析多只股票
- **中文PDF**：Render会自动安装wqy字体，PDF可正常生成
- **API Key安全**：硅基流动Key和Tushare Token在页面实时输入，不写进代码，不会泄露

### 升级为付费版（可选）

如果需要24小时不休眠 + 更快速度，选 **Starter** 方案（$7/月）。

---

## API Key 获取

| 功能 | 需要 | 获取地址 |
|------|------|---------|
| 全市场选股 / 单股分析 | Tushare Token | https://tushare.pro/register |
| 新闻AI分析 | 硅基流动 API Key | https://cloud.siliconflow.cn |

硅基流动新用户送14元额度，DeepSeek-V3免费版每日100次，每次分析约3毛钱。
