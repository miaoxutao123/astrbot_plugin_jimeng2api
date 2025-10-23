"""
Jimeng Python 客户端完整功能测试

在运行前请将 SESSION_ID 替换为自己的有效 session。
该脚本将顺序执行：
1. 服务生命周期检查
2. Session 状态检查
3. 积分查询
4. 文生图（URL 与 Base64）
5. 图生图（基于生成的图片 URL）
6. 文生视频

注意：文生视频耗时较长且会消耗积分，请谨慎运行。
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import List

# 确保可以导入插件目录下的本地模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pyjimeng.jimeng_service import JimengAPIService  # noqa: E402
from pyjimeng.errors import JimengAPIError  # noqa: E402

SESSION_ID = ""

TEXT_PROMPT = "暮色下的赛博朋克城市街景，霓虹灯倒映在雨后的街道上"
COMPOSITION_PROMPT = "请让场景色彩更鲜艳，突出霓虹和街道的雨后反射"
VIDEO_PROMPT = "无人机缓慢环绕未来城市夜景，灯光闪烁，天空略带薄雾"


def _assert_url_list(items: List[dict]) -> None:
    if not items:
        raise AssertionError("返回数据为空")
    for item in items:
        if "url" not in item or not item["url"].startswith("http"):
            raise AssertionError(f"返回项缺少有效 URL: {item}")


def main() -> None:
    if SESSION_ID.startswith("REPLACE"):
        raise SystemExit("请先在 tests/full_jimeng_service_test.py 中设置 SESSION_ID")

    service = JimengAPIService(session_id=SESSION_ID, auto_start=False)

    print("===> 测试：服务启动/关闭")
    service.start()
    assert service.is_running(), "服务未处于运行状态"
    service.stop()
    assert not service.is_running(), "服务停止失败"
    service.start()

    try:
        print("===> 测试：Session 状态检查")
        status = service.check_session_status()
        print("Session 是否存活:", status.get("live"))
        assert status.get("live") is not None, "Session 状态检查返回异常"

        print("===> 测试：积分查询")
        points = service.get_points()
        if points:
            info = points[0]["points"]
            print(
                f"总积分: {info['totalCredit']} "
                f"(赠送: {info['giftCredit']}, 购买: {info['purchaseCredit']}, VIP: {info['vipCredit']})"
            )
        else:
            print("未返回积分信息")

        print("===> 测试：文生图（URL 返回）")
        image_result = service.generate_image(
            prompt=TEXT_PROMPT,
            model="jimeng-4.0",
            ratio="1:1",
            resolution="1k",
            response_format="url",
        )
        _assert_url_list(image_result["data"])
        first_image_url = image_result["data"][0]["url"]
        print("生成图片 URL:", first_image_url)

        print("===> 测试：文生图（Base64 返回）")
        image_b64_result = service.generate_image(
            prompt="请生成同一场景的速写风格版本",
            model="jimeng-4.0",
            ratio="1:1",
            resolution="1k",
            response_format="b64_json",
        )
        b64_item = image_b64_result["data"][0]
        assert "b64_json" in b64_item, "Base64 返回缺少 b64_json 字段"
        # 简单校验 Base64 是否可解码
        base64.b64decode(b64_item["b64_json"])
        print("Base64 图片生成成功（输出省略）")

        print("===> 测试：图生图（使用第一张文生图）")
        composition_result = service.image_composition(
            prompt=COMPOSITION_PROMPT,
            images=[first_image_url],
            model="jimeng-4.0",
            ratio="1:1",
            resolution="1k",
            response_format="url",
        )
        _assert_url_list(composition_result["data"])
        print("图生图 URL:", composition_result["data"][0]["url"])

        print("===> 测试：文生视频")
        video_result = service.generate_video(
            prompt=VIDEO_PROMPT,
            model="jimeng-video-3.0",
            width=960,
            height=540,
            resolution="720p",
            response_format="url",
        )
        _assert_url_list(video_result["data"])
        print("视频 URL:", video_result["data"][0]["url"])

        print("\n[OK] 全部测试通过")

    except JimengAPIError as exc:
        print(f"\n[ERR] API 调用失败: {exc}", file=sys.stderr)
        raise
    finally:
        service.stop()


if __name__ == "__main__":
    main()
