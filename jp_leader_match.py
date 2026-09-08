"""Recover an English leader card code from a Japanese archetype label.

The Japanese meta source (tcg-portal) labels each deck only by an archetype
name like ``緑ミホーク`` ("Green Mihawk") plus a picture -- it never gives the
leader's card code the way the Western source does. Without a code a JP deck
can't link to its leader page, and its tournament result can't feed into that
leader's page. This module maps the archetype -> a leader ``card_id`` by
translating the colour prefix + character name and matching it against the
Leader cards we already have, so JP decks join the same leader pages as West.

Matching is deliberately conservative: colour combo AND character must line up.
When several printings of the same leader match, the set suffix in the label
(e.g. ``赤エース（OP16）``) disambiguates; failing that we take the newest set.
Anything we can't place confidently is left blank (no wrong links).
"""
from __future__ import annotations

import re

JP_COLOR = {'赤': 'Red', '青': 'Blue', '緑': 'Green', '紫': 'Purple', '黄': 'Yellow', '黒': 'Black'}

# Katakana character -> the English name as it appears inside our leader card
# names (substring match, so "Luffy" hits "Monkey.D.Luffy", "Linlin" hits
# "Charlotte Linlin", etc.).
JP_LEADER = {
    'ミホーク': 'Mihawk', 'ルフィ': 'Luffy', 'エネル': 'Enel', 'エース': 'Ace', 'ロビン': 'Robin',
    'ロックス': 'Rocks', 'サボ': 'Sabo', 'ハンコック': 'Hancock', 'ティーチ': 'Teach', 'クザン': 'Kuzan',
    'リンリン': 'Linlin', 'ゾロ': 'Zoro', 'ナミ': 'Nami', 'ヤマト': 'Yamato', 'ルーシー': 'Lucy',
    'イム': 'Im', 'カタクリ': 'Katakuri', 'ドフラミンゴ': 'Doflamingo', 'カイドウ': 'Kaido',
    'シャンクス': 'Shanks', 'ロー': 'Law', 'キッド': 'Kid', 'ボニー': 'Bonney', 'ウタ': 'Uta',
    'モリア': 'Moria', 'クロ': 'Kuro', 'ペローナ': 'Perona', 'プリン': 'Pudding', 'バギー': 'Buggy',
    'スモーカー': 'Smoker', 'クマ': 'Kuma', 'レベッカ': 'Rebecca', 'ドレーク': 'Drake', 'リューマ': 'Ryuma',
    'ベロ': 'Belo Betty', 'ガープ': 'Garp', 'クイーン': 'Queen', 'ボア': 'Hancock', 'ゲッコー': 'Moria',
    'ロシナンテ': 'Rosinante', 'クロコダイル': 'Crocodile', 'ジンベエ': 'Jinbe', 'サンジ': 'Sanji',
    'ロジャー': 'Roger', 'コビー': 'Koby', 'ビビ': 'Vivi', 'クリーク': 'Krieg', 'フォクシー': 'Foxy',
    'ルッチ': 'Lucci', 'レイリー': 'Rayleigh', 'ベガパンク': 'Vegapunk', 'カルガラ': 'Kalgara',
}

_SFX = re.compile(r"[（(]([^）)]*)[）)]\s*$")
_SETNUM = re.compile(r"(?:OP|EB|ST|PRB)(\d+)", re.IGNORECASE)


def parse_archetype(name: str):
    """Return (colors:list, english_leader:str, set_suffix:str) for a JP label."""
    s = (name or "").strip()
    sfx = ""
    m = _SFX.search(s)
    if m:
        sfx = m.group(1).strip()
        s = s[:m.start()].strip()
    colors = []
    while s and s[0] in JP_COLOR:
        colors.append(JP_COLOR[s[0]])
        s = s[1:]
    return colors, JP_LEADER.get(s, s), sfx


def _setnum(card_id: str) -> int:
    m = _SETNUM.match(card_id or "")
    return int(m.group(1)) if m else -1


def load_leaders(db) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT card_id,name,card_color FROM cards WHERE card_type='Leader'")]


def resolve_leader_id(leaders: list[dict], name: str) -> str:
    """Best-effort leader card_id for a JP archetype label; '' when unsure."""
    colors, en, sfx = parse_archetype(name)
    if not colors or not en:
        return ""
    tgt = set(colors)
    cands = [L for L in leaders
             if set((L.get('card_color') or '').split()) == tgt
             and en.lower() in (L.get('name') or '').lower()]
    if len(cands) == 1:
        return cands[0]['card_id']
    if len(cands) > 1:
        if sfx:
            key = sfx.upper().replace('-', '')
            narrowed = [L for L in cands
                        if key in (L['card_id'] + ' ' + (L.get('name') or '')).upper().replace('-', '')]
            if len(narrowed) == 1:
                return narrowed[0]['card_id']
        # Same character + colours across sets, nothing to disambiguate on:
        # the newest printing is the safest single guess.
        cands.sort(key=lambda L: _setnum(L['card_id']), reverse=True)
        return cands[0]['card_id']
    return ""


def backfill(db) -> int:
    """Fill leader_id for JP decks that don't have one yet. Idempotent."""
    leaders = load_leaders(db)
    rows = db.execute(
        "SELECT id,event_name FROM meta_decks "
        "WHERE country='JP' AND (leader_id IS NULL OR leader_id='')").fetchall()
    n = 0
    for r in rows:
        lid = resolve_leader_id(leaders, r[1] or "")
        if lid:
            db.execute("UPDATE meta_decks SET leader_id=? WHERE id=?", (lid, r[0]))
            n += 1
    db.commit()
    return n
