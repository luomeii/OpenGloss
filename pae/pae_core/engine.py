"""PAE engine service logic (no HTTP). Events -> states -> annotations."""
import hashlib
import re
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path

from .db import (append_event, get_db, import_lemma_as_dict, init_schema,
                 load_events, lookup_word)
from .dict_loader import IndexedTranslationLookup, load_exchange_map, load_translation_lookup
from .fold import fold_event, score_at
from .contacts import CONTACT_SELECTED, SELECTED_TYPES, project_contact
from .lemmatize import candidates, load_lemma_map
from .state import DEFAULTS

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")

# 60 common English function words, filtered before annotation.
STOPWORDS = frozenset("""
a an the and or but if then than that this these those
of in on at to for with by from as
is are was were be been being am
do does did have has had
it its he she they them his her their
we you i me my your our
not no so up out about
must also very only most much many more less least
while when where which who whom whose what how why
there here now just even still yet again
can could shall should will would may might
let lets get got gets make makes made
too such own same other another each any every all both
come came go went take took give gave
see seen look looks find found know knew think thought
say said tell told ask asked use used using
first second next last new old good great
""".split())

# --- 可调参数：环境变量 > pae/config.json > 内置缺省 ---
import os as _os
import json as _json
from pathlib import Path as _Path

_cfg_path = _Path(__file__).resolve().parents[1] / 'config.json'
try:
    _cfg = _json.loads(_cfg_path.read_text(encoding='utf-8'))
except Exception:
    _cfg = {}

ANNOTATE_MAX_S = float(_os.environ.get('PAE_MAX_S', _cfg.get('annotate_max_s', 0.75)))
BUDGET = int(_os.environ.get('PAE_BUDGET', _cfg.get('budget', 8)))
# 选择策略（2026-10-02 算法复审 D2 修复）：band_priority=学习带优先（默认）/ text_order=旧的文本顺序
SELECTION_MODE = str(_os.environ.get('PAE_SELECTION_MODE', _cfg.get('selection_mode', 'band_priority')))
# 已知高频词过滤：bnc(BNC 词频排名，越小越高频) > 0 且 <= 该值则选择层跳过。
# 同时修「HN 首页预算被 comment/show/job/submit 这类常见词吃光」。
BNC_KNOWN_RANK = int(_os.environ.get('PAE_BNC_RANK', _cfg.get('bnc_known_rank', 3000)))

# 路径统一由 paths.py 解析（env > config.json > 相对默认）——不再写死绝对路径，项目可迁移。
# 兼容旧环境变量：PAE_DB_PATH / PAE_LEMMA_PATH / PAE_DICT_PATH 仍然生效。
from . import paths as _paths

DB_PATH = _paths.db_path()
LEMMA_PATH = _paths.lemma_path()
DICT_SQLITE_PATH = _paths.dict_path()


class LearningWritePaused(RuntimeError):
    """A feedback commit failed; restart before accepting more learning writes."""


class Engine:
    """Append-only event log + deterministic fold replay + annotation budget."""

    def __init__(self, db_path, lemma_map_path):
        self.db_path = str(db_path)
        self.lemma_map_path = str(lemma_map_path)
        self.conn = get_db(self.db_path)
        init_schema(self.conn)
        import_lemma_as_dict(self.conn, self.lemma_map_path)
        self.lemma_map = load_lemma_map(self.lemma_map_path)
        self.exchange_map = load_exchange_map(DICT_SQLITE_PATH)
        self.translations = load_translation_lookup(DICT_SQLITE_PATH)
        try:
            # 有效参数 = 算法常数 ⊕ 引擎旋钮 ⊕ 运行时覆盖（AI 提案改的就是这层）
            try:
                from . import params as _params_mod
                self.params = _params_mod.effective(self.conn)
            except Exception:
                self.params = dict(DEFAULTS)
            self.lock = threading.Lock()
            self.states = {}
            self.seen_idem = set()
            self._replay()
            # Only learning events use this connection. Other writers can no longer
            # commit an annotate batch that belongs to this engine.
            self.learning_conn = get_db(self.db_path)
        except BaseException:
            self._close_translation_lookup()
            raise

    def _close_translation_lookup(self):
        if isinstance(self.translations, IndexedTranslationLookup):
            self.translations.close()

    def _learning_connection(self):
        # Synthetic Engine.__new__ fixtures from earlier stages have only conn.
        return getattr(self, 'learning_conn', self.conn)

    def _replay(self):
        self.contacts = {}
        for ev in load_events(self.conn, include_visible_opportunity=False):
            payload = ev.get("payload") or {}
            folded = dict(payload)
            folded["ts"] = ev["ts"]
            folded["type"] = ev["type"]
            folded["idem_key"] = ev["idem_key"]
            if "lemma_id" not in folded and folded.get("lemma"):
                folded["lemma_id"] = folded["lemma"]
            if ev["type"] in SELECTED_TYPES and ev["idem_key"] not in self.seen_idem:
                project_contact(self.contacts, folded)
            if ev["type"] == CONTACT_SELECTED:
                if ev["idem_key"] is not None:
                    self.seen_idem.add(ev["idem_key"])
                continue
            fold_event(self.states, folded, self.params, self.seen_idem)

    def reduce_chain(self, tok):
        """四层还原链：原词 -> candidates(lemma_map/规则) -> exchange_map 链式
        (最多 2 跳，去重防循环)。返回有序去重候选列表，原词在前。
        """
        word = tok.lower()
        seen = set()
        ordered = []

        def add(item):
            if item and item not in seen:
                seen.add(item)
                ordered.append(item)

        add(word)
        for cand in candidates(word, self.lemma_map):
            add(cand)
        frontier = list(ordered)
        for _ in range(2):
            nxt = []
            for cand in frontier:
                base = self.exchange_map.get(cand)
                if base and base not in seen:
                    seen.add(base)
                    ordered.append(base)
                    nxt.append(base)
            if not nxt:
                break
            frontier = nxt
        return ordered

    def annotate(self, text, page_id, session_id=None, max_annotations=None):
        self._ensure_learning_writable()
        if max_annotations is not None and (type(max_annotations) is not int or max_annotations < 0):
            raise ValueError('max_annotations must be a non-negative integer')
        budget = BUDGET if max_annotations is None else min(BUDGET, max_annotations)
        if budget == 0:
            return []
        text = text or ""
        page_id = str(page_id)
        # 参数陈旧就刷新（最多滞后 5 秒）——ttl 到期自动回滚靠这个生效
        if time.time() - getattr(self, "_params_ts", 0.0) > 5.0:
            self.refresh_params()
        # 熟悉度：每 5 分钟最多重算一次（shadow 模式也算，好让标定数据有东西看）
        if time.time() - getattr(self, "_fam_ts", 0.0) > 300.0:
            try:
                from . import capsurface as _cs
                from . import familiarity as _fam
                self._fam_mode = _cs._fam_mode()
                if self._fam_mode != "off":
                    _fam.recompute(self.conn, self.params, self.states)
                    self._fam_exempt = set(_fam.exempt_set(self.conn))
                else:
                    self._fam_exempt = set()
            except Exception:
                self._fam_exempt = set()
            self._fam_ts = time.time()
        tokens = [t.lower() for t in TOKEN_RE.findall(text)]
        now = int(time.time())
        day_bucket = now // 86400
        # ---- 选择层 Phase 1（2026-10-02 算法复审 D2：让学习带真正参与选择）----
        # 旧行为（text_order）：文本顺序取前 BUDGET 个有释义的词——学习状态只剩 S<max_s 的
        #   二元闸，26 号的「学习带」只做报表，核心机制名存实亡（独立复审判定）。
        # 新行为（band_priority，默认）：pinned 最优先（保住钉住语义）→ 带内词按 |S-带中点|
        #   升序（最贴学习边缘的先）→ 带外词垫后；同分保持文本序（稳定排序）。
        # 注意：这里的 S 是**记账前**的当前值（选择依据先验状态，再记本次曝光）。
        _cands = []
        _seen = set()
        for tok in tokens:
            if tok in STOPWORDS or len(tok) < 3:
                continue
            _chain = self.reduce_chain(tok)
            _lemma = None
            for _cand in reversed(_chain):
                if _cand in self.translations:
                    _lemma = _cand
                    break
            if _lemma is None:
                _lemma = tok
            if _lemma in _seen:
                continue
            _entry = self.translations.get(_lemma)
            if not (_entry and _entry[0] and (_entry[0] or "").strip()):
                continue
            _bnc = _entry[1] if _entry and len(_entry) > 1 else 0
            if _bnc and 0 < _bnc <= BNC_KNOWN_RANK:
                continue
            _seen.add(_lemma)
            _st0 = self.states.get((_lemma, 0))
            _s0 = score_at(_st0, self.params, now)
            _cands.append((_lemma, _s0,
                           bool(_st0 is not None and getattr(_st0, 'pinned', False))))
        if SELECTION_MODE == 'band_priority' and _cands:
            _band_lo = float(self.params.get('band_lo', 0.10))
            _band_hi = float(self.params.get('band_hi', 0.55))
            _mid = (_band_lo + _band_hi) / 2.0

            # 本页今天已注解过的词（= 之前分块请求已给过曝光）。
            # 2026-10-02 回归修正（t8 实锤）：旧版优先级让「已带内词」在每个分块都重复霸占预算，
            # 同一长页 3 个分块只产出 7-8 个不同词。修正后的优先序：
            #   3 = pinned（钉住永远最优先）
            #   2 = 带内 且 本页未注解过（**新曝光**——这才是选择层该干的活）
            #   1 = 带外 但 本页未注解过（生词垫后，保持覆盖面）
            #   0 = 本页已注解过的重复词（预算允许时才回填，让后文出现处也能高亮）
            _now_ts = now
            _suppress_s = 3 * 86400   # M12-C：点「认识了」后 3 天短抑制

            def _prio(_c):
                _idem = hashlib.sha1(
                    ("%s|%s|%d" % (page_id, _c[0], day_bucket)).encode("utf-8")).hexdigest()[:20]
                _repeat = _idem in self.seen_idem
                if _c[2]:
                    return 3
                if _repeat:
                    return 0
                # M12-C：3 天内点过「认识了」的词压到最低优先（当天安静）；
                # 但仍在 chosen 候选里（预算富余时照记账——E 继续涨，3 天后不诈尸）
                _stx = self.states.get((_c[0], 0))
                if _stx is not None and _stx.t_known is not None and (_now_ts - _stx.t_known) < _suppress_s:
                    return 0
                return 2 if _band_lo <= _c[1] <= _band_hi else 1

            _cands.sort(key=lambda _c: (-_prio(_c), abs(_c[1] - _mid)))
            # Read eligibility and contact flags together, only for this batch.
            with self.lock:
                _a_positions = []
                eligible_candidates = []
                for index, candidate in enumerate(_cands):
                    key = (candidate[0], 0)
                    if key not in self.states and _prio(candidate) == 1:
                        _a_positions.append(index)
                        eligible_candidates.append((candidate, key in self.contacts))
            eligible_candidates.sort(key=lambda item: item[1])
            for index, (candidate, _) in zip(_a_positions, eligible_candidates):
                _cands[index] = candidate
        chosen = set(_c[0] for _c in _cands[:budget])
        # Keep the existing learning lock across the entire batch. Push and
        # record_event cannot enter a second learning transaction mid-request.
        with self.lock:
            self._ensure_learning_writable()
            out = self._append_annotate_batch(
                tokens, chosen, text, page_id, session_id, day_bucket, now, budget)
        # 主动说话（问题一修复，2026-10-03）：规则版自发触发，补上引擎侧唯一缺失的 say 生产者。
        # 2026-10-03 二次反馈升级：命中触发时可能走 LLM（1-2 秒）——绝不能挂在注解响应里
        # 拖慢页面（用户红线：不许拖慢阅读），改为**后台线程派发**：每个引擎实例 ≥300s
        # 派发一次；push 侧还有自己的节流/限流做第二道闸。异常一律吞掉——
        # 绝不能因为「她想说话」把注解主链路拖垮。SQLite 已是 WAL + busy_timeout=30s，
        # 后台线程写 push_log 与前台写 events 可安全并发。
        try:
            from . import push as _push_mod
            if now - float(getattr(self, "_proactive_dispatch_ts", 0.0)) >= _push_mod.PROACTIVE_EVAL_INTERVAL_S:
                self._proactive_dispatch_ts = float(now)
                threading.Thread(target=_push_mod.maybe_proactive,
                                 args=(self.conn, self, now), daemon=True).start()
        except Exception:
            pass
        return out

    def _append_annotate_batch(self, tokens, chosen, text, page_id, session_id,
                               day_bucket, now, budget):
        """Stage changed words and publish only after the batch is durable."""
        conn = self._learning_connection()
        staged = {}
        staged_contacts = {}
        staged_seen = set()
        out = []
        first_seen = set()
        try:
            out = self._stage_annotate_words(
                conn, staged, staged_seen, tokens, chosen, text, page_id,
                session_id, day_bucket, now, budget, first_seen, out, staged_contacts)
            try:
                conn.commit()
            except Exception:
                self._learning_write_fault = True
                raise LearningWritePaused('learning_write_paused: restart required')
        except Exception:
            # This connection contains only learning events. A rollback cannot
            # discard a push, audit, or memory write from the main connection.
            try:
                conn.rollback()
            except Exception:
                conn.close()
            raise
        self.states.update(staged)
        self.contacts.update(staged_contacts)
        self.seen_idem.update(staged_seen)
        return out

    def _stage_annotate_words(self, conn, staged, staged_seen, tokens, chosen,
                              text, page_id, session_id, day_bucket, now,
                              budget, first_seen, out, staged_contacts):
        for tok in tokens:
            if tok in STOPWORDS:
                continue
            if len(tok) < 3:  # P0-3 2b: 单/双字符 token 无学习价值
                continue
            chain = self.reduce_chain(tok)
            # 选择策略：在词典命中的候选中取链条最靠后的（最还原形）。
            # 变形词(intensified/threads)在词典里有独立词条，但记账户必须归一到原形(intensify/thread)。
            lemma = None
            for cand in reversed(chain):
                if cand in self.translations:
                    lemma = cand
                    break
            if lemma is None:
                lemma = tok
            if lemma in first_seen:
                continue
            entry = self.translations.get(lemma)
            gloss = (entry[0] or "").strip() if entry and entry[0] else ""
            if not gloss:  # P0-3 2a: 无释义不注（用户名/拼错词不进账本、不占预算）
                continue
            bnc = entry[1] if entry and len(entry) > 1 else 0
            if bnc and 0 < bnc <= BNC_KNOWN_RANK:  # P0-3 2c: 已知高频词跳过
                continue
            first_seen.add(lemma)
            if lemma not in chosen:   # 只记被选中的词（选择已由 Phase 1 决定）
                continue
            idem_key = hashlib.sha1(
                ("%s|%s|%d" % (page_id, lemma, day_bucket)).encode("utf-8")
            ).hexdigest()[:20]
            payload = {
                "lemma_id": lemma,
                "lemma": lemma,
            }
            # 内存判重优先：seen_idem 命中则不碰数据库
            if idem_key in self.seen_idem or idem_key in staged_seen:
                event_id = None
            else:
                event_id = append_event(conn, CONTACT_SELECTED, payload,
                                        idem_key=idem_key, source="api",
                                        autocommit=False)
                if event_id is not None:
                    fact = dict(payload)
                    fact["ts"] = conn.execute(
                        'SELECT ts FROM events WHERE event_id=?', (event_id,)).fetchone()[0]
                    fact["type"] = CONTACT_SELECTED
                    key = (lemma, 0)
                    if key not in staged_contacts and key in self.contacts:
                        staged_contacts[key] = self.contacts[key]
                    project_contact(staged_contacts, fact)
                    staged_seen.add(idem_key)
            st = staged.get((lemma, 0), self.states.get((lemma, 0)))
            s = score_at(st, self.params, now)
            # 熟悉度影子集：**仅 enforce 模式**才真的跳过（默认 shadow=只记录，行为零变化）
            if getattr(self, "_fam_mode", None) == "enforce":
                if lemma in self._fam_exempt:
                    continue
            # 注解门槛取「有效参数」（可被 AI 提案调整）；pinned/mastered 对齐 26 号 decide() 语义
            max_s = float(self.params.get("annotate_max_s", ANNOTATE_MAX_S))
            if st is not None and getattr(st, "mastered", False):
                continue
            if s < max_s or (st is not None and getattr(st, "pinned", False)):
                out.append({
                    "surface": tok,
                    "lemma": lemma,
                    "gloss": gloss,
                    "s_value": round(s, 3),
                    "counted": event_id is not None,
                })
            if len(out) >= budget:
                break
        return out

    def _ensure_learning_writable(self):
        if getattr(self, '_learning_write_fault', False):
            raise LearningWritePaused('learning_write_paused: restart required')

    def _feedback_state_locked(self, lemma, now):
        """Only committed rows can confirm feedback; do not read our pending transaction."""
        idem = hashlib.sha1(('known|%s|%d' % (lemma, now // 86400)).encode('utf-8')).hexdigest()[:20]
        uri = Path(self.db_path).resolve().as_uri() + '?mode=ro'
        observer = sqlite3.connect(uri, uri=True, timeout=1)
        try:
            row = observer.execute('SELECT 1 FROM events WHERE idem_key=?', (idem,)).fetchone()
        finally:
            observer.close()
        st = self.states.get((lemma, 0))
        return {'ok': True, 'lemma': lemma, 'recorded': row is not None,
                'valid_until': (now // 86400 + 1) * 86400,
                's_value': round(score_at(st, self.params, now), 3)}

    def feedback_state(self, lemma):
        lemma = (lemma or '').strip()
        if not lemma:
            return {'ok': False, 'error': 'empty lemma'}
        with self.lock:
            if getattr(self, '_learning_write_fault', False):
                return {'ok': False, 'error': 'learning_write_paused', 'restart_required': True}
            return self._feedback_state_locked(lemma, int(time.time()))

    def record_event(self, etype, lemma, surface="", metas=None, source="ext"):
        """扩展上报的观察/交互事件（三证据的发射口）。

        - annotation_shown：纯观察（fold 的 _OBS_ONLY），不参与 S 推导，按 (lemma, 小时) 去重
        - hover：+0.3 权重，按 (lemma, session, 小时) 去重（防悬停刷分）
        - known_click：沿用有效 w_known，按 (lemma, 天) 去重（防连点）
        """
        observation = etype == 'visible_opportunity'
        if etype not in ("annotation_shown", "hover", "known_click", "user_pin", "user_mastered", "visible_opportunity"):
            return {"ok": False, "error": "unknown event type: " + str(etype)}
        lemma = (lemma or "").strip()
        if not lemma:
            return {"ok": False, "error": "empty lemma"}
        now = int(time.time())
        m = metas or {}
        if observation:
            # Client report, never learning evidence. Do not accept page text/URLs.
            if (surface or set(m) != {'page_id', 'visible_ms', 'observation_version'} or
                    not re.fullmatch(r"[a-z][a-z'-]{2,63}", lemma) or
                    not isinstance(m.get('page_id'), str) or
                    not re.fullmatch(r'd[a-z0-9]{10,80}', m['page_id']) or
                    type(m.get('visible_ms')) is not int or not 1000 <= m['visible_ms'] <= 60000 or
                    type(m.get('observation_version')) is not int or m['observation_version'] != 1):
                return {'ok': False, 'counted': False, 'error': 'invalid_visible_opportunity'}
            bucket_key = 'visible-v1|%s|%d' % (lemma, now // 86400)
        elif etype == "known_click":
            bucket_key = "known|%s|%d" % (lemma, now // 86400)
        elif etype == "hover":
            bucket_key = "hover|%s|%s|%d" % (lemma, m.get("session_id") or "", now // 3600)
        else:
            bucket_key = "shown|%s|%s|%d" % (lemma, m.get("page_id") or "", now // 3600)
        idem_key = hashlib.sha1(bucket_key.encode("utf-8")).hexdigest()[:20]
        payload = {"lemma_id": lemma, "lemma": lemma}
        if not observation:
            payload['surface'] = surface or lemma
        fields = ('page_id', 'visible_ms', 'observation_version') if observation else (
            'page_id', 'session_id', 's_value', 'page_active_ms', 'sentence')
        for k in fields:
            if m.get(k) is not None:
                payload[k] = m[k]
        with self.lock:
            if getattr(self, '_learning_write_fault', False):
                return {'ok': False, 'counted': False, 'error': 'learning_write_paused',
                        'restart_required': True}
            conn = self._learning_connection()
            if idem_key in self.seen_idem:
                return {"ok": True, "counted": False, "reason": "duplicate_bucket"}
            if observation:
                st = self.states.get((lemma, 0))
                if (st is None or st.n_enc < 1) and (lemma, 0) not in self.contacts:
                    return {'ok': False, 'counted': False, 'error': 'observation_without_encounter'}
                if conn.execute('SELECT 1 FROM events WHERE idem_key=?', (idem_key,)).fetchone():
                    return {'ok': True, 'counted': False, 'reason': 'duplicate_bucket', 'observation_only': True}
                used = int(conn.execute(
                    "SELECT COUNT(*) FROM events WHERE type='visible_opportunity' AND ts>=? AND ts<?",
                    (now // 86400 * 86400, (now // 86400 + 1) * 86400)).fetchone()[0])
                if used >= 1000:
                    return {'ok': False, 'counted': False, 'error': 'visible_opportunity_daily_limit',
                            'used_today': used, 'limit': 1000}
            # H2 配额（评审 2026-10-03）：known_click 直接 +4.5 进 S 值（M12-C），批量伪造
            # 可静默关掉任意词的注解——(lemma,天) 去重挡不住换词投毒。按类型日总量设闸：
            # 真人一天点 60 个「认识了」已属重度，攻击循环会被这里掐断。
            if etype == "known_click":
                feedback = self._feedback_state_locked(lemma, now)
                if feedback['recorded']:
                    return {'ok': True, 'counted': False, 'reason': 'duplicate_bucket',
                            's_value': feedback['s_value'], 'feedback': feedback}
                day0 = now // 86400 * 86400
                kc_today = int(conn.execute(
                    "SELECT COUNT(*) FROM events WHERE type='known_click' AND ts>=?",
                    (day0,)).fetchone()[0])
                if kc_today >= 60:
                    return {"ok": False, "counted": False,
                            "error": "known_click_daily_limit", "used_today": kc_today, "limit": 60}
            try:
                event_id = append_event(conn, etype, payload, idem_key=idem_key,
                                        source=source, autocommit=False)
                counted = event_id is not None
                staged = {}
                staged_seen = set()
                folded_params = dict(self.params) if etype == 'known_click' else self.params
                if counted and not observation:
                    folded = dict(payload)
                    folded["ts"] = now
                    folded["type"] = etype
                    folded["idem_key"] = idem_key
                    key = (lemma, 0)
                    if key in self.states:
                        staged[key] = replace(self.states[key])
                    fold_event(staged, folded, folded_params, staged_seen)
                try:
                    conn.commit()
                except Exception:
                    self._learning_write_fault = True
                    raise
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    conn.close()
                if getattr(self, '_learning_write_fault', False):
                    return {'ok': False, 'counted': False, 'error': 'learning_write_paused',
                            'result_unknown': True, 'restart_required': True}
                raise
            self.states.update(staged)
            self.seen_idem.update(staged_seen)
            if observation:
                return {'ok': True, 'counted': counted, 'observation_only': True}
            st = self.states.get((lemma, 0))
            s = round(score_at(st, self.params, now), 3)
            if etype == 'known_click':
                return {'ok': True, 'counted': counted, 's_value': s,
                        'feedback': self._feedback_state_locked(lemma, now)}
        # 悬停 = 最强兴趣信号 → 异步跑义项判定影子（P5 第一步：只写缓存与日志，不上屏）
        if etype == "hover" and (metas or {}).get("sentence") and lemma:
            try:
                import threading as _th
                _th.Thread(target=self._shadow_judge, args=(lemma, metas.get("sentence")),
                           daemon=True, name="pae-sensejudge").start()
            except Exception:
                pass
        return {"ok": True, "counted": counted, "s_value": s}

    def _shadow_judge(self, lemma, sentence):
        """义项判定影子：失败静默（不影响任何用户可见行为）。"""
        try:
            from . import sensejudge as _sj
            from . import llm as _llm_mod
            from . import user_controls as _controls
            if not _controls.allowed("shadow"):
                return
            with _llm_mod.call_purpose("shadow"):
                _sj.judge(self.conn, DICT_SQLITE_PATH, lemma, sentence)
        except Exception:
            pass

    def refresh_params(self):
        """重新载入有效参数（提案生效后调用）。"""
        try:
            from . import params as _params_mod
            self.params = _params_mod.effective(self.conn)
        except Exception:
            pass
        self._params_ts = time.time()
        return self.params

    def status(self):
        with self.lock:
            self._ensure_learning_writable()
            now = int(time.time())
            rows = self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
            events = int(rows["n"]) if rows else 0
            lex = self.conn.execute("SELECT COUNT(*) AS n FROM dict_words").fetchone()
            lexicon = int(lex["n"]) if lex else 0
            top = sorted(
                ((key[0], score_at(st, self.params, now)) for key, st in self.states.items()),
                key=lambda kv: (-kv[1], kv[0]),
            )[:20]
        return {
            "events": events,
            "lexicon": lexicon,
            "top_words": [(lemma, round(s, 4)) for lemma, s in top],
        }


_ENGINE = None
_ENGINE_LOCK = threading.Lock()
_ENGINE_STOPPING = threading.Event()


def get_engine() -> Engine:
    """Process-wide lazy singleton on the fixed production paths."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE_STOPPING.is_set():
            raise RuntimeError('dictionary service is stopping')
        if _ENGINE is None:
            _ENGINE = Engine(DB_PATH, LEMMA_PATH)
        return _ENGINE


def close_dictionary_on_shutdown():
    """Reject late warmup and drain initialization before releasing the lookup."""
    _ENGINE_STOPPING.set()
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            _ENGINE._close_translation_lookup()
