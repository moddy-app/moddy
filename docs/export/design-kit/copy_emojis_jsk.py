import aiohttp, asyncio, base64, io, json, pathlib, re
A = "https://discord.com/api/v10"
async def job(tok):
    H = {"Authorization": "Bot " + tok}
    msg = await channel.send("Copie des emojis : preparation...")
    async with aiohttp.ClientSession() as s:
        async def rq(m, p, **k):
            while True:
                async with s.request(m, A + p, headers=H, **k) as r:
                    if r.status == 429:
                        await asyncio.sleep((await r.json()).get("retry_after", 1) + 0.5)
                        continue
                    if r.status >= 400:
                        raise RuntimeError(f"{m} {p.split('/')[-1]} -> {r.status} {(await r.text())[:200]}")
                    return await r.json()
        aid = (await rq("GET", "/applications/@me"))["id"]
        have = {e["name"]: int(e["id"]) for e in (await rq("GET", f"/applications/{aid}/emojis"))["items"]}
        old = {}
        for e in await bot.fetch_application_emojis():
            old[e.id] = (e.name, e.animated)
        try:
            from minibots.emojis import moddy_emojis
            for i, v in (await asyncio.to_thread(moddy_emojis)).items():
                old.setdefault(i, v)
        except Exception:
            pass
        try:
            from utils.emojis import DARK_VARIANTS
            for n, i in DARK_VARIANTS.items():
                old.setdefault(int(i), (n, False))
        except Exception:
            pass
        todo, seen = [], set()
        for i, (n, an) in old.items():
            if n not in seen:
                seen.add(n)
                if n not in have:
                    todo.append((i, n, an))
        ok, ko, fails = 0, 0, []
        for k, (i, n, an) in enumerate(todo, 1):
            ext = "gif" if an else "png"
            try:
                async with s.get(f"https://cdn.discordapp.com/emojis/{i}.{ext}") as r:
                    if r.status != 200:
                        raise RuntimeError(f"CDN {r.status}")
                    img = base64.b64encode(await r.read()).decode()
                e = await rq("POST", f"/applications/{aid}/emojis",
                             json={"name": n, "image": f"data:image/{ext};base64,{img}"})
                have[n] = int(e["id"])
                ok += 1
            except Exception as x:
                ko += 1
                fails.append(f"{n}: {x}")
            if k % 10 == 0:
                await msg.edit(content=f"Copie des emojis : {k}/{len(todo)} (ok {ok}, echecs {ko})")
        new = {e["name"]: {"id": e["id"], "animated": bool(e.get("animated")), "markdown": f"<{'a' if e.get('animated') else ''}:{e['name']}:{e['id']}>"}
               for e in sorted((await rq("GET", f"/applications/{aid}/emojis"))["items"], key=lambda e: e["name"])}
    idmap = {i: have[n] for i, (n, _) in old.items() if n in have}
    mapping = {str(i): {"name": old[i][0], "new": str(v)} for i, v in idmap.items()}
    swap = lambda t: re.sub(r"\d{17,20}", lambda m: str(idmap.get(int(m.group()), m.group())), t)
    import utils.emojis as ue
    J = lambda d, f: discord.File(io.BytesIO(json.dumps(d, indent=1).encode()), f)
    files = [J(new, "nouveaux_emojis.json"), J(mapping, "emoji_mapping.json"),
             discord.File(io.BytesIO(swap(pathlib.Path(ue.__file__).read_text()).encode()), "emojis.py")]
    if fails:
        files.append(discord.File(io.BytesIO("\n".join(fails).encode()), "echecs.txt"))
    await msg.edit(content=f"Copie terminee : {ok} copies, {len(have) - ok} deja presents, {ko} echecs (app {aid}).")
    await channel.send(files=files)
async def run(tok):
    try:
        await job(tok)
    except Exception as x:
        await channel.send(f"Copie des emojis interrompue : {x}"[:1900])
# >>> Target bot token below (bottom line: never shown in the jsk panel)
bot.spawn(run("TOKEN_DU_BOT_CIBLE"), name="emoji-copy")
return "Copie lancee en arriere-plan : suis la progression dans ce salon."
