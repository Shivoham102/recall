import pathlib
import asyncio


async def file_create(inp: dict) -> dict:
    filename = inp["filename"]
    content = inp.get("content", "")
    directory = inp.get("directory", "~/Documents")

    path = pathlib.Path(directory).expanduser() / filename
    await asyncio.to_thread(lambda: (path.parent.mkdir(parents=True, exist_ok=True), path.write_text(content, encoding="utf-8")))

    return {
        "summary": f"Created {path}",
        "path": str(path),
    }
