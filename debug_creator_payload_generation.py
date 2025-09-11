# FILE: debug_creator_payload_generation.py
# CHẠY TRONG MÔI TRƯỜNG CỦA PROJECT CREATOR
# Mục đích: Debug quá trình tạo payload cuối cùng, tập trung vào FileQualityValidator.

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List

# --- CONFIGURATION: Chỉ định đường dẫn đến project đã chạy để debug ---
# THAY ĐỔI ĐƯỜNG DẪN NÀY để trỏ đến thư mục output của một job đã chạy
PROJECT_TO_DEBUG_PATH = r"D:\OUTPUT\spr-nk-shifted-original-version" 
# ---------------------------------------------------------------------

# Cấu hình logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('CreatorPayloadDebug')

def main():
    logger.info("="*60)
    logger.info("🚀 STARTING CREATOR PAYLOAD GENERATION DEBUGGER 🚀")
    logger.info(f"Debugging project folder: {PROJECT_TO_DEBUG_PATH}")
    logger.info("="*60)
    
    project_path = Path(PROJECT_TO_DEBUG_PATH)
    if not project_path.is_dir():
        logger.error(f"Thư mục project không tồn tại: {PROJECT_TO_DEBUG_PATH}")
        return

    # 1. Tải các module cần thiết từ project Creator
    try:
        from creator_api_client import CreatorService
        from sprunki_ultimate_master import ProcessingItem
        from config_manager import initialize_config
        # Quan trọng: Tải FileQualityValidator để kiểm tra
        from file_validator import FileQualityValidator
        
        logger.info("✅ Tải các module của Creator thành công.")
    except ImportError as e:
        logger.error(f"❌ Lỗi import module. Hãy đảm bảo bạn đang chạy script này từ thư mục gốc của project Creator. Lỗi: {e}")
        return

    # 2. Khởi tạo các đối tượng cần thiết
    try:
        # Khởi tạo config để các module khác có thể hoạt động
        initialize_config() 
        # Tạo một instance giả lập của CreatorService để gọi hàm
        # (chúng ta không cần kết nối API thật)
        service_instance = CreatorService()
        logger.info("✅ Khởi tạo các instance thành công.")
    except Exception as e:
        logger.error(f"❌ Lỗi khi khởi tạo instance: {e}")
        return

    # 3. Mô phỏng một ProcessingItem đã hoàn thành
    logger.info("\n--- STEP 1: Simulating a completed ProcessingItem ---")
    
    # Tìm tất cả các file video trong thư mục
    output_files = [str(p) for p in project_path.glob('*.mkv')] + \
                   [str(p) for p in project_path.glob('*.mp4')]

    if not output_files:
        logger.error("Không tìm thấy file video nào trong thư mục. Dừng debug.")
        return
        
    simulated_item = ProcessingItem(
        project_slug=project_path.name,
        output_files=output_files
    )
    logger.info(f"Đã mô phỏng item với {len(simulated_item.output_files)} file output:")
    for f in simulated_item.output_files:
        logger.info(f"  - {Path(f).name}")

    # 4. Chạy hàm _organize_output_files để tạo payload
    logger.info("\n--- STEP 2: Executing _organize_output_files ---")
    logger.info("Đây là bước quan trọng nhất, nó sẽ gọi FileQualityValidator.")
    
    try:
        final_payload = service_instance._organize_output_files(simulated_item.output_files)
        logger.info("✅ Hàm _organize_output_files đã chạy xong.")
    except Exception as e:
        logger.error("❌ Lỗi nghiêm trọng khi chạy _organize_output_files:", exc_info=True)
        final_payload = None

    # 5. Phân tích kết quả payload
    logger.info("\n--- STEP 3: Analyzing the generated payload ---")
    
    if final_payload is None:
        logger.error("Payload cuối cùng là None. Có lỗi xảy ra.")
        return

    print("\n" + "-"*30 + " FINAL PAYLOAD " + "-"*30)
    print(json.dumps(final_payload, indent=2))
    print("-" * 75 + "\n")

    # Kiểm tra các vấn đề phổ biến
    master_list = final_payload.get('master')
    
    if master_list is None:
        logger.error("❌ VẤN ĐỀ NGHIÊM TRỌNG: Key 'master' không tồn tại trong payload!")
    elif not isinstance(master_list, list):
        logger.error(f"❌ VẤN ĐỀ NGHIÊM TRỌNG: Key 'master' không phải là một danh sách (list), mà là {type(master_list)}!")
    elif not master_list:
        logger.error("❌ VẤN ĐỀ NGHIÊM TRỌNG: Danh sách 'master' bị rỗng!")
        logger.error("Lý do có thể là:")
        logger.error("  1. Logic phân loại file trong _organize_output_files đã sai, không nhận diện được file 'showcase'.")
        logger.error("  2. FileQualityValidator đã xác định tất cả các file master là không hợp lệ (invalid) và loại bỏ chúng.")
        logger.error("  3. Có lỗi trong quá trình xử lý kết quả validation (_process_validation_results).")
    else:
        logger.info("✅ Payload có vẻ hợp lệ. Key 'master' là một danh sách không rỗng.")

    logger.info("\n--- STEP 4: Deep dive into FileQualityValidator (if needed) ---")
    logger.info("Chạy lại validation với log chi tiết cho file đầu tiên.")
    
    try:
        validator = FileQualityValidator()
        first_video = simulated_item.output_files[0]
        logger.info(f"Đang kiểm tra chi tiết file: {first_video}")
        # Chạy lại một lần nữa để xem chi tiết
        detailed_result = validator.validate_file(first_video)
        print("\n" + "-"*30 + " DETAILED VALIDATION RESULT " + "-"*30)
        print(json.dumps(detailed_result, indent=2))
        print("-" * 88 + "\n")
        
        if detailed_result.get('technical_analysis', {}).get('error'):
            logger.error("❌ Lỗi phân tích kỹ thuật! Rất có thể:")
            logger.error("  - FFmpeg (chứa ffprobe) chưa được cài đặt hoặc không nằm trong PATH của hệ thống.")
            logger.error(f"  - Lỗi chi tiết: {detailed_result['technical_analysis']['error']}")
        elif not detailed_result.get('is_valid'):
             logger.warning("⚠️ File bị đánh dấu là không hợp lệ (invalid).")
             logger.warning(f"   Lý do: {detailed_result.get('validation_errors')}")
        else:
            logger.info("✅ Phân tích chi tiết file đầu tiên không có lỗi nghiêm trọng.")

    except Exception as e:
        logger.error("❌ Lỗi khi chạy FileQualityValidator một cách chi tiết:", exc_info=True)


if __name__ == "__main__":
    main()