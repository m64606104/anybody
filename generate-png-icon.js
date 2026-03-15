import sharp from 'sharp';
import fs from 'fs';

// 读取 SVG 文件
const svgContent = fs.readFileSync('./public/favicon.svg', 'utf8');

// 转换为 180x180 PNG
sharp(Buffer.from(svgContent))
  .resize(180, 180)
  .png()
  .toFile('./public/apple-touch-icon.png')
  .then(() => {
    console.log('✅ apple-touch-icon.png 已生成 (180x180)');
  })
  .catch(err => {
    console.error('❌ 生成失败:', err);
  });
