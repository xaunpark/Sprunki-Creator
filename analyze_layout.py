import cv2
import numpy as np
import os
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

# --- CẤU HÌNH ---
INPUT_FOLDER = 'scr' 

# --- CẤU HÌNH GIỚI HẠN CỨNG (PIXEL) ---
TOP_LINE_RANGE_PX = (150, 250)
BOTTOM_LINE_RANGE_PX = (600, 750)

# --- CẤU HÌNH GIÁ TRỊ DỰ PHÒNG (FALLBACK) ---
TOP_LINE_FALLBACK_Y = 170
BOTTOM_LINE_FALLBACK_Y = 670

# --- CẤU HÌNH BỘ LỌC: KIỂM TRA TÍNH LIỀN MẠCH ---
CONTINUITY_CHECK_SLICE_HEIGHT = 10
CONTINUITY_THRESHOLD_RATIO = 0.85

# --- Cấu hình tìm đỉnh ban đầu ---
PEAK_HEIGHT_THRESHOLD_RATIO = 0.10
PEAK_MIN_DISTANCE_RATIO = 0.05


def is_line_continuous(edges_slice, width_threshold):
    """
    Kiểm tra một lát cắt của ảnh cạnh để xem nó có đủ liền mạch hay không.
    Trả về True nếu liền mạch, False nếu là hàng icon đứt đoạn.
    """
    vertical_projection = np.sum(edges_slice, axis=0)
    non_empty_columns = np.count_nonzero(vertical_projection)
    continuity_ratio = non_empty_columns / edges_slice.shape[1]
    return continuity_ratio >= width_threshold

def find_dividing_lines(image_path):
    """
    Phân tích ảnh dựa trên giới hạn cứng và áp dụng giá trị dự phòng nếu không tìm thấy.
    """
    print(f"\n{'='*20} Phân tích ảnh: {os.path.basename(image_path)} {'='*20}")

    image = cv2.imread(image_path)
    if image is None: return None, None, None, None

    img_height, img_width, _ = image.shape
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray_image, 50, 150)
    horizontal_projection = np.sum(edges, axis=1)

    candidate_peaks_indices, properties = find_peaks(
        horizontal_projection,
        height=img_width * PEAK_HEIGHT_THRESHOLD_RATIO,
        distance=img_height * PEAK_MIN_DISTANCE_RATIO
    )
    all_candidate_peaks = {p: properties['peak_heights'][i] for i, p in enumerate(candidate_peaks_indices)}
    
    print(f"🔎 Tìm thấy {len(all_candidate_peaks)} đỉnh ứng cử viên.")

    # 2. Lọc và tìm đường trên
    top_line_y = None
    top_candidates = {y: h for y, h in all_candidate_peaks.items() if TOP_LINE_RANGE_PX[0] <= y <= TOP_LINE_RANGE_PX[1]}
    
    if top_candidates:
        continuous_top_lines = {}
        for y, height in top_candidates.items():
            slice_start = max(0, y - CONTINUITY_CHECK_SLICE_HEIGHT // 2)
            slice_end = min(img_height, y + CONTINUITY_CHECK_SLICE_HEIGHT // 2)
            edge_slice = edges[slice_start:slice_end, :]
            
            if is_line_continuous(edge_slice, CONTINUITY_THRESHOLD_RATIO):
                continuous_top_lines[y] = height
        
        if continuous_top_lines:
            top_line_y = max(continuous_top_lines, key=continuous_top_lines.get)
            print(f"✅ Vùng trên (Y={TOP_LINE_RANGE_PX[0]}-{TOP_LINE_RANGE_PX[1]}): Tìm thấy đường liền mạch mạnh nhất tại Y ≈ {top_line_y}")
        else:
            top_line_y = max(top_candidates, key=top_candidates.get)
            print(f"⚠️ Vùng trên (Y={TOP_LINE_RANGE_PX[0]}-{TOP_LINE_RANGE_PX[1]}): Không có đường liền mạch. Chọn đỉnh mạnh nhất tại Y ≈ {top_line_y}")
    
    # --- BẮT ĐẦU CƠ CHẾ DỰ PHÒNG (FALLBACK) CHO ĐƯỜNG TRÊN ---
    if top_line_y is None:
        top_line_y = TOP_LINE_FALLBACK_Y
        print(f"⚠️ Không tìm thấy đỉnh nào trong vùng tìm kiếm trên. Áp dụng giá trị dự phòng Y={top_line_y}")

    # 3. Lọc và tìm đường dưới
    bottom_line_y = None
    bottom_candidates = {y: h for y, h in all_candidate_peaks.items() if BOTTOM_LINE_RANGE_PX[0] <= y <= BOTTOM_LINE_RANGE_PX[1]}

    if bottom_candidates:
        continuous_bottom_lines = {}
        for y, height in bottom_candidates.items():
            slice_start = max(0, y - CONTINUITY_CHECK_SLICE_HEIGHT // 2)
            slice_end = min(img_height, y + CONTINUITY_CHECK_SLICE_HEIGHT // 2)
            edge_slice = edges[slice_start:slice_end, :]

            if is_line_continuous(edge_slice, CONTINUITY_THRESHOLD_RATIO):
                continuous_bottom_lines[y] = height
        
        if continuous_bottom_lines:
            bottom_line_y = max(continuous_bottom_lines, key=continuous_bottom_lines.get)
            print(f"✅ Vùng dưới (Y={BOTTOM_LINE_RANGE_PX[0]}-{BOTTOM_LINE_RANGE_PX[1]}): Tìm thấy đường liền mạch mạnh nhất tại Y ≈ {bottom_line_y}")
        else:
            bottom_line_y = max(bottom_candidates, key=bottom_candidates.get)
            print(f"⚠️ Vùng dưới (Y={BOTTOM_LINE_RANGE_PX[0]}-{BOTTOM_LINE_RANGE_PX[1]}): Không có đường liền mạch. Chọn đỉnh mạnh nhất tại Y ≈ {bottom_line_y}")

    # --- BẮT ĐẦU CƠ CHẾ DỰ PHÒNG (FALLBACK) CHO ĐƯỜNG DƯỚI ---
    if bottom_line_y is None:
        bottom_line_y = BOTTOM_LINE_FALLBACK_Y
        print(f"⚠️ Không tìm thấy đỉnh nào trong vùng tìm kiếm dưới. Áp dụng giá trị dự phòng Y={bottom_line_y}")

    return (top_line_y, bottom_line_y), edges, horizontal_projection, candidate_peaks_indices

def visualize_results(image_path, edges, projection, all_peaks, final_lines):
    # (Hàm này giữ nguyên, không thay đổi)
    original_image = cv2.cvtColor(cv2.imread(image_path), cv2.COLOR_BGR2RGB)
    
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(20, 6))
    fig.suptitle(f'Kết quả phân tích cho: {os.path.basename(image_path)}', fontsize=16)

    ax1.set_title('Ảnh gốc và Đường phân cách')
    ax1.imshow(original_image)
    if final_lines and final_lines[0] is not None:
        ax1.axhline(y=final_lines[0], color='cyan', linestyle='--', linewidth=2, label=f'Đường trên (Y={final_lines[0]})')
    if final_lines and final_lines[1] is not None:
        ax1.axhline(y=final_lines[1], color='lime', linestyle='--', linewidth=2, label=f'Đường dưới (Y={final_lines[1]})')
    ax1.legend()

    ax2.set_title('Ảnh sau khi dùng Canny Edge')
    ax2.imshow(edges, cmap='gray')

    ax3.set_title('Biểu đồ Mật độ Cạnh theo Chiều ngang')
    ax3.plot(projection, range(len(projection))) 
    if all_peaks is not None and len(all_peaks) > 0:
        ax3.plot(projection[all_peaks], all_peaks, "x", color='orange', markersize=4, alpha=0.7, label='Tất cả các đỉnh tiềm năng')
    if final_lines and final_lines[0] is not None:
         ax3.plot(projection[final_lines[0]], final_lines[0], "o", color='cyan', markersize=10, label=f'Đỉnh được chọn (Trên)')
    if final_lines and final_lines[1] is not None:
         ax3.plot(projection[final_lines[1]], final_lines[1], "o", color='lime', markersize=10, label=f'Đỉnh được chọn (Dưới)')
    ax3.set_xlabel('Mật độ (tổng số pixel cạnh)')
    ax3.set_ylabel('Tọa độ Y của ảnh')
    ax3.invert_yaxis() 
    ax3.grid(True, linestyle='--', alpha=0.6)
    ax3.legend()
    
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.show()

# --- HÀM CHÍNH ĐỂ CHẠY SCRIPT ---
if __name__ == "__main__":
    if not os.path.isdir(INPUT_FOLDER):
        print(f"Lỗi: Thư mục '{INPUT_FOLDER}' không tồn tại.")
        print("Vui lòng tạo thư mục và đặt các file ảnh chụp màn hình vào đó.")
        os.makedirs(INPUT_FOLDER)
        print(f"Đã tự động tạo thư mục '{INPUT_FOLDER}'.")
    else:
        image_files = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]

        if not image_files:
            print(f"Không tìm thấy file ảnh nào trong thư mục '{INPUT_FOLDER}'.")
        else:
            for filename in image_files:
                path = os.path.join(INPUT_FOLDER, filename)
                lines, edge_img, proj, all_pks = find_dividing_lines(path)
                if edge_img is not None:
                    visualize_results(path, edge_img, proj, all_pks, lines)