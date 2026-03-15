#!/usr/bin/env python3
import base64
from PIL import Image, ImageDraw
import io

# 创建 180x180 的图像
img = Image.new('RGBA', (180, 180), (255, 255, 255, 0))
draw = ImageDraw.Draw(img)

# 绘制浅蓝色圆形背景
draw.ellipse([0, 0, 180, 180], fill=(230, 242, 255, 255))

# 绘制蓝色小熊爪
# 掌心
draw.ellipse([54, 81, 126, 153], fill=(59, 130, 246, 255))

# 四个脚趾
# 左边小脚趾
draw.ellipse([36, 54, 72, 90], fill=(59, 130, 246, 255))
# 中间偏左脚趾
draw.ellipse([63, 27, 99, 63], fill=(59, 130, 246, 255))
# 中间偏右脚趾
draw.ellipse([81, 27, 117, 63], fill=(59, 130, 246, 255))
# 右边小脚趾
draw.ellipse([108, 54, 144, 90], fill=(59, 130, 246, 255))

# 保存为不同尺寸的图标
img.save('./public/apple-touch-icon.png', 'PNG')
print('✅ apple-touch-icon.png 已生成 (180x180)')

# 32x32
img32 = img.resize((32, 32), Image.Resampling.LANCZOS)
img32.save('./public/favicon-32x32.png', 'PNG')
print('✅ favicon-32x32.png 已生成')

# 16x16
img16 = img.resize((16, 16), Image.Resampling.LANCZOS)
img16.save('./public/favicon-16x16.png', 'PNG')
print('✅ favicon-16x16.png 已生成')
