FROM python:3.9-slim

WORKDIR /app

# 时区设为上海（交易时间依赖）
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 数据目录（由 volume 挂载，首次启动自动初始化）
VOLUME /app/db /app/data

EXPOSE 5000

CMD ["python", "app.py"]
