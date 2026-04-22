import os

# ===================== 【重要！在这里设置你要整理的文件夹路径】 =====================
TARGET_FOLDER = r"E:\wcy\Everybody！Play！\RandomSeiyu\跨企划大牌"  # 改成你要批量编号的文件夹


# =====================================================================================

def rename_folders_with_continuous_number(folder_path):
    if not os.path.isdir(folder_path):
        print(f"❌ 文件夹不存在：{folder_path}")
        return

    # 1. 收集所有子文件夹
    all_items = []
    for name in os.listdir(folder_path):
        full_path = os.path.join(folder_path, name)
        if os.path.isdir(full_path) and '-' in name:
            all_items.append(name)

    if not all_items:
        print("✅ 没有找到需要重命名的声优文件夹！")
        return

    # 2. 提取名字（丢掉旧编号，只保留名字）
    name_list = []
    for folder_name in all_items:
        _, seiyu_name = folder_name.split('-', 1)
        seiyu_name = seiyu_name.strip()
        name_list.append(seiyu_name)

    # 3. 去重 + 排序（让顺序更美观）
    name_list = sorted(list(set(name_list)))
    print(f"📌 找到 {len(name_list)} 个声优，即将从 001 开始连续编号...\n")

    # 4. 预览要修改的内容（安全第一！）
    print("=" * 50)
    print("【预览重命名结果】")
    print("=" * 50)
    for idx, name in enumerate(name_list, 0):
        new_name = f"{idx:03d}-{name}"
        print(f"  → {new_name}")
    print("=" * 50)

    # 5. 确认是否执行
    confirm = input("\n是否确认重命名？（输入 Y 确认，其他取消）：")
    if confirm.strip().upper() != 'Y':
        print("❌ 已取消重命名！")
        return

    # 6. 开始重命名
    success = 0
    for idx, name in enumerate(name_list, 0):
        new_folder_name = f"{idx:03d}-{name}"
        new_folder_path = os.path.join(folder_path, new_folder_name)

        # 找一个旧文件夹用来改名（任意一个同名旧文件夹）
        old_folder_name = None
        for fname in all_items:
            if fname.split('-', 1)[1].strip() == name:
                old_folder_name = fname
                break

        if not old_folder_name:
            continue

        old_path = os.path.join(folder_path, old_folder_name)
        new_path = os.path.join(folder_path, new_folder_name)

        try:
            os.rename(old_path, new_path)
            print(f"✅ {old_folder_name}  →  {new_folder_name}")
            success += 1
        except Exception as e:
            print(f"❌ 重命名失败：{old_folder_name}，原因：{str(e)}")

    print(f"\n🎉 完成！成功重命名 {success} 个文件夹！")


if __name__ == "__main__":
    rename_folders_with_continuous_number(TARGET_FOLDER)
