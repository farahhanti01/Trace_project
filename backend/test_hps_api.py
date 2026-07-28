import asyncio

from dotenv import load_dotenv

from app.services.hps_ai_service import call_hps_ai


load_dotenv()
         

async def main() -> None:
    content = await call_hps_ai(
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant.",
            },
            {
                "role": "user",
                "content": "Reply only with OK.",
            },
        ],
    )

    print("Response:", content)


if __name__ == "__main__":
    asyncio.run(main())
