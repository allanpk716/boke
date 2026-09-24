# -*- coding: utf-8 -*-
"""生成试听网站素材:预设音色样本 + 圆脸克隆样本 + 汇集已有音频 → work/web/"""
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "work" / "web"
AUDIO = WEB / "audio"

SAMPLE = "大家好,今天我们来聊一聊最近国际上发生的大事,欢迎收听剥壳节目。"

# (voice_id, 展示名, 性别/风格)
VOICES = [
    ("zh-CN-YunxiNeural", "云希", "男·阳光清爽"),
    ("zh-CN-YunjianNeural", "云健", "男·沉稳浑厚"),
    ("zh-CN-YunyangNeural", "云扬", "男·新闻播音"),
    ("zh-CN-YunxiaNeural", "云夏", "男·少年音"),
    ("zh-CN-XiaoxiaoNeural", "晓晓", "女·温暖亲切"),
    ("zh-CN-XiaoyiNeural", "晓伊", "女·活泼轻快"),
    ("zh-CN-liaoning-XiaobeiNeural", "小北(东北)", "女·东北口音"),
    ("zh-CN-shaanxi-XiaoniangNeural", "小娘(陕西)", "女·陕西口音"),
    ("zh-TW-HsiaoChenNeural", "曉臻(台湾)", "女·台湾普通话"),
    ("zh-HK-HiuMaanNeural", "曉曼(粤语)", "女·粤语"),
]


async def gen_voice(vid: str, out: Path):
    import edge_tts
    c = edge_tts.Communicate(SAMPLE, vid)
    await c.save(str(out))


def gen_presets():
    import edge_tts  # noqa: F401
    meta = []
    for vid, name, style in VOICES:
        out = AUDIO / f"preset_{vid.replace('-', '_')}.mp3"
        if not out.exists():
            for attempt in range(3):
                try:
                    asyncio.run(gen_voice(vid, out))
                    if out.stat().st_size > 1000:
                        break
                except Exception as e:
                    print(f"[web] {vid} attempt {attempt+1} fail: {e}")
                    out.unlink(missing_ok=True)
        if out.exists():
            meta.append({"file": out.name, "name": name, "style": style,
                         "voice_id": vid})
            print(f"[web] preset ok: {name}")
        else:
            print(f"[web] preset MISSING: {vid}")
    (WEB / "presets.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def gen_clone_sample():
    """圆脸克隆读同一句(zero 模式,用户已判 zero 更好)"""
    out = AUDIO / "clone_yuanlian_sample.wav"
    if out.exists():
        return str(out.name)
    sys.path.insert(0, str(ROOT / "src"))
    from boke.synthesize import CosyVoiceEngine
    eng = CosyVoiceEngine(repo_dir="D:/boke_media/tools/CosyVoice",
                          model_dir="D:/boke_media/models/Fun-CosyVoice3-0.5B-2512")
    import yaml
    cfg = yaml.safe_load((ROOT / "src" / "boke" / "voices.yaml").read_text(encoding="utf-8"))
    ref = ROOT / cfg["library"]["yuanlian_chinese"]["ref_audio"]
    ref_text = (ROOT / "refs" / "ref01_transcript.txt").read_text(encoding="utf-8").strip()
    eng.synth(SAMPLE, ref, ref_text, out)
    print(f"[web] clone sample ok: {out.name}")
    return str(out.name)


def collect_existing():
    copies = [
        (ROOT / "refs/yuanlian_chinese_01.wav", "ref_yuanlian_01.wav"),
        (ROOT / "refs/yuanlian_chinese_02.wav", "ref_yuanlian_02.wav"),
        (ROOT / "out/clone_test_1.wav", "clone_test_1_cross.wav"),
        (ROOT / "out/clone_test_2.wav", "clone_test_2_cross.wav"),
        (ROOT / "out/clone_test_3.wav", "clone_test_3_cross.wav"),
        (ROOT / "out/clone_test_1_zero.wav", "clone_test_1_zero.wav"),
        (ROOT / "out/clone_test_2_zero.wav", "clone_test_2_zero.wav"),
        (ROOT / "out/clone_test_3_zero.wav", "clone_test_3_zero.wav"),
        (ROOT / "out/BV17UPszhE6o_full.m4a", "product_v1_full.m4a"),
        (ROOT / "out/smoke_BV17UPszhE6o_5to15.m4a", "product_v1_slice10min.m4a"),
    ]
    for src, name in copies:
        if src.exists():
            shutil.copy2(src, AUDIO / name)
    # 追加: 原片人声样本(分说话人)待真实 diarization 后生成 speaker_SPKxx_*.wav
    spk = sorted(AUDIO.glob("speaker_*.wav"))
    return [c[1] for c in copies if (ROOT / c[0]).exists()] + [s.name for s in spk]


def build_index(presets, clone_file, files):
    preset_rows = "\n".join(
        f"""<tr><td>{p['name']}</td><td>{p['style']}</td>
        <td><audio controls preload="none" src="audio/{p['file']}"></audio></td>
        <td><select class="pick" data-voice="{p['voice_id']}" data-name="{p['name']}">
        <option value="">— 不选 —</option>
        <option>SPK_00</option><option>SPK_01</option><option>SPK_02</option><option>SPK_03</option>
        </select></td></tr>""" for p in presets)

    def audio_row(f, label):
        return (f'<div class="item"><span>{label}</span>'
                f'<audio controls preload="none" src="audio/{f}"></audio></div>')

    spk_files = [f for f in files if f.startswith("speaker_")]
    spk_html = ""
    if spk_files:
        spk_html = ("<h2>⑤ 新一期视频的原声说话人样本(先听这个,再决定谁配什么音)</h2>"
                    + "".join(audio_row(f, f.replace("speaker_", "").replace(".wav", "")) for f in spk_files))

    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>剥壳配音 · 试听台</title><style>
body{{font-family:system-ui,'Microsoft YaHei';margin:0;background:#f5f6f8;color:#222}}
header{{background:#1c2b3a;color:#fff;padding:14px 22px}}
header h1{{margin:0;font-size:20px}} header p{{margin:4px 0 0;font-size:13px;opacity:.75}}
main{{max-width:880px;margin:18px auto;padding:0 14px}}
section{{background:#fff;border-radius:10px;padding:16px 18px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
h2{{font-size:16px;margin:0 0 10px;border-left:4px solid #2b6cb0;padding-left:8px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
td,th{{padding:7px 6px;border-bottom:1px solid #eee;text-align:left;vertical-align:middle}}
audio{{width:230px;height:34px}}
.item{{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #f0f0f0;gap:12px}}
.item span{{font-size:14px}}
.note{{font-size:13px;color:#666;margin-top:8px}}
textarea{{width:100%;min-height:64px;margin-top:10px;box-sizing:border-box}}
button{{background:#2b6cb0;color:#fff;border:0;border-radius:6px;padding:9px 22px;font-size:15px;cursor:pointer;margin-top:12px}}
button:disabled{{background:#999}}
#status{{margin-left:12px;font-size:14px}}
.tag{{display:inline-block;background:#e8f0fe;color:#2b6cb0;border-radius:4px;font-size:12px;padding:1px 7px;margin-left:8px}}
</style></head><body>
<header><h1>剥壳配音 · 试听台</h1>
<p>开发机音频出口不可用,本页供工作机浏览器试听与选择</p></header>
<main>

<section><h2>① 嘉宾预设音色(选音色:先听,右侧下拉分配给说话人)</h2>
<table><tr><th>音色</th><th>风格</th><th>试听(同一句话)</th><th>分配给</th></tr>
{preset_rows}</table>
<div class="note">都读同一句:「{SAMPLE}」</div></section>

<section><h2>② 博主(圆脸)克隆音色<span class="tag">你已判 zero 模式更好</span></h2>
{audio_row(clone_file, '克隆·zero模式·样本句(与上面预设同句对比)')}
{audio_row('clone_test_2_zero.wav', '克隆·zero·测试句2(银行/行长多音字)')}
{audio_row('ref_yuanlian_01.wav', '参考音原声(对照像不像)')}
</section>

<section><h2>③ 参考音存档(你已确认可以)</h2>
{audio_row('ref_yuanlian_02.wav', '参考音02')}
</section>

<section><h2>④ 成品 v1(你已判:太快/扎耳/轮换不自然 → v2 制作中)</h2>
{audio_row('product_v1_slice10min.m4a', '冒烟 10 分钟版')}
{audio_row('product_v1_full.m4a', '全片 39:58 版')}
<div class="note">v2 改进:真实声纹分人(替代轮流假数据)+ 整句合并合成(不再逐碎句)+ 圆脸换克隆音色 + 变速上限收紧</div></section>

{spk_html}

<section><h2>提交你的选择</h2>
<textarea id="memo" placeholder="备注:比如「云健给主持人,晓晓给嘉宾女,克隆再自然点」…"></textarea>
<button onclick="save()">保存我的选择</button><span id="status"></span></section>

</main><script>
async function save(){{
  const picks = [];
  document.querySelectorAll('select.pick').forEach(s => {{
    if (s.value) picks.push({{spk: s.value, voice_id: s.dataset.voice, name: s.dataset.name}});
  }});
  const body = {{picks, memo: document.getElementById('memo').value,
                 ts: new Date().toLocaleString()}};
  const st = document.getElementById('status');
  try {{
    const r = await fetch('/save_picks', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
    st.textContent = r.ok ? '✅ 已保存,回去告诉 Claude 一声即可' : '❌ 保存失败 ' + r.status;
  }} catch(e) {{ st.textContent = '❌ 网络错误'; }}
}}
</script></body></html>"""
    (WEB / "index.html").write_text(html, encoding="utf-8")
    print(f"[web] index.html built ({len(html)} bytes)")


def main():
    AUDIO.mkdir(parents=True, exist_ok=True)
    presets = gen_presets()
    clone_file = gen_clone_sample()
    files = collect_existing()
    build_index(presets, clone_file, files)
    print("[web] assets ready:", len(list(AUDIO.iterdir())), "files")


if __name__ == "__main__":
    main()
