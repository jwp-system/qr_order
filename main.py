import uvicorn

# ----------------------------------------------------
# 메인 파일 직접 실행 설정
# ----------------------------------------------------
if __name__ == "__main__":
    # reload=True 사용 시 문자열 형식("main:app" 또는 "app.main:app")으로 전달해야 함
    uvicorn.run(
        "app2.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )