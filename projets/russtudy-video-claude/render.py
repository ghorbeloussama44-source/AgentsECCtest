import asyncio, sys, base64, pathlib
from playwright.async_api import async_playwright
V = pathlib.Path(sys.argv[1]); mode = sys.argv[2]  # "keys" ou "all"
FONT = base64.b64encode((V / "sg-500.woff2").read_bytes()).decode()
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = await b.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        await pg.goto((V / "anim.html").as_uri())
        await pg.add_style_tag(content="@font-face{font-family:'Space Grotesk';src:url(data:font/woff2;base64,%s) format('woff2');font-weight:300 700;}"
                               "html,body{margin:0!important;padding:0!important;background:#0A0A0A!important;display:block!important;}"
                               "canvas{display:block!important;width:1080px!important;height:1920px!important;max-width:none!important;max-height:none!important;}" % FONT)
        await pg.evaluate("document.fonts.load('500 100px \"Space Grotesk\"').then(()=>document.fonts.load('700 100px \"Space Grotesk\"'))")
        canvas = pg.locator("canvas").first
        times = [float(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [0.6, 1.8, 3.0, 5.0, 7.4, 9.3] if mode == "keys" else [i / 30 for i in range(300)]
        out = V / ("keys" if mode == "keys" else "frames"); out.mkdir(exist_ok=True)
        for i, t in enumerate(times):
            await pg.evaluate(f"window.renderAt({t})")
            await canvas.screenshot(path=str(out / (f"k{t:.1f}.png" if mode == "keys" else f"f{i:04d}.png")))
        print("frames", len(times), "errors", errs[:5])
        await b.close()
asyncio.run(main())
