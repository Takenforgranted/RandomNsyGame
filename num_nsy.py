import os
import random
from PIL import Image
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

# ===================== 【配置】清晰缩略图 =====================
SUPPORT_FORMATS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif')
IMAGE_DISPLAY_SIZE = (200, 200)  # 清晰缩略图大小
ROW_HEIGHT = 180                  # 行高适配
COL_WIDTHS = [12, 30, 25]         # 列宽适配
OUTPUT_EXCEL = "女声优图鉴统计表.xlsx"

# ===================== 工具函数 =====================
def get_random_image(folder_path):
    images = []
    for f in os.listdir(folder_path):
        if f.lower().endswith(SUPPORT_FORMATS):
            img_path = os.path.join(folder_path, f)
            try:
                with Image.open(img_path) as img:
                    img.verify()
                images.append(img_path)
            except:
                continue
    return random.choice(images) if images else None

def resize_image(img_path, target_size):
    """ 高质量等比例缩放，保证清晰 """
    try:
        img = Image.open(img_path).convert("RGB")
        # 🔥 高质量缩放，不模糊
        img.thumbnail(target_size, Image.Resampling.LANCZOS)
        background = Image.new("RGB", target_size, "white")
        offset = ((target_size[0] - img.width) // 2, (target_size[1] - img.height) // 2)
        background.paste(img, offset)
        return background
    except:
        return Image.new("RGB", target_size, "white")

# ===================== 主程序 =====================
if __name__ == "__main__":
    wb = Workbook()
    wb.remove(wb.active)

    for item in os.listdir("./assets"):
        if not os.path.isdir(os.path.join("./assets", item)) or item.startswith("."):
            continue

        project_name = item
        print(f"正在处理企划：{project_name}")

        ws = wb.create_sheet(title=project_name[:30])
        ws.append(["编号", "女声优名字", "代表图片"])

        # 居中样式
        center = Alignment(horizontal='center', vertical='center')
        for col in range(1, 4):
            ws.cell(row=1, column=col).alignment = center

        # 收集声优文件夹
        seiyu_dirs = []
        for subfolder in os.listdir(os.path.join("./assets", project_name)):
            sf_path = os.path.join("./assets", project_name, subfolder)
            if os.path.isdir(sf_path) and '-' in subfolder:
                seiyu_dirs.append((subfolder, sf_path))

        # 按编号排序
        try:
            seiyu_dirs.sort(key=lambda x: int(x[0].split('-')[0]))
        except:
            pass

        row = 2
        for folder_name, folder_path in seiyu_dirs:
            parts = folder_name.split('-', 1)
            sid = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else "未知"

            ws.cell(row=row, column=1, value=sid).alignment = center
            ws.cell(row=row, column=2, value=name).alignment = center

            # 插入清晰缩略图
            img_path = get_random_image(folder_path)
            if img_path:
                resized_img = resize_image(img_path, IMAGE_DISPLAY_SIZE)
                temp_img = f"_tmp_{row}_{random.randint(1000,9999)}.jpg"
                resized_img.save(temp_img, quality=95)  # 🔥 保存高质量

                xl_img = XLImage(temp_img)
                # 🔥 不再强行拉伸！保持清晰缩略图
                # xl_img.width = 800  <-- 已删除
                # xl_img.height = 800 <-- 已删除

                ws.add_image(xl_img, f"C{row}")

            ws.row_dimensions[row].height = ROW_HEIGHT
            row += 1

        # 设置列宽
        for i, w in enumerate(COL_WIDTHS, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    wb.save(OUTPUT_EXCEL)
    print(f"\n✅ 导出完成：{OUTPUT_EXCEL}")

    # 清理临时文件
    for f in os.listdir("."):
        if f.startswith("_tmp_") and f.endswith(".jpg"):
            try:
                os.remove(f)
            except:
                pass
