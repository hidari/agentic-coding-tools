---
status: open
---

# fix: web-monkey-qa でブラウザが作り直される条件と、Basic 認証の無い section での検知

## 背景

ISSUE-109 の (4) から切り出した。別のリポジトリのセッションの報告では、`agent-browser` (0.39.1) のブラウザが `press Escape` の直後などに作り直され、cookie と Basic 認証が消えた。7 回中 3 回で、再現の条件は取れていない (報告者の実測)。

ISSUE-109 で確かめたこと (2026-11-13、agent-browser 0.39.1、ローカルの 2 origin):

- 作り直しは再現しなかった。`open --headers` の 1 セッションで `press Escape` → `snapshot -i` → 同じ origin のリンクのクリックを 7 回、`set credentials` の 3 セッションを並列にして同じ操作を 7 回ずつ (遷移は 21 回) 回し、どちらも 401 は 0 回だった
- 作り直されたときの見え方は、セッションを `close` してから同じ名前で `open` し直して作った。`open` は rc 1 と `net::ERR_INVALID_AUTH_CREDENTIALS` で失敗し、network の射影には `Document` の 401 が出た
- この 401 を目印に、ISSUE-109 で explorer に「Basic 認証の section で `BASE_URL` の `Document` が 401 になったら打ち切る」検知を入れた
- `close` の直後に同じセッション名で打った `open` は `Invalid response: EOF` で返り、実行されたかどうかが分からなかった。少し間を空けると上のとおり実行された

残っていること:

- 作り直しの条件は分かっていない。報告者の環境は Basic 認証の背後の staging で、3 体の explorer が 1 つの wave で並列に動いていた。手元の 2 origin と違う点 (実在のアプリ、TLS、ページ側のスクリプト、報告者の環境の負荷) のどれが効いているかは確かめていない
- Basic 認証の無い section (`auth: seed_login` だけで cookie のログインに頼るもの) では、cookie が消えても 401 は出ない。ISSUE-109 の検知はこの場合に効かず、ログインページへ戻されたまま探索を続けうる

## タスク

- [ ] 報告と同じ条件 (実在のアプリ、Basic 認証の背後、3 並列) で作り直しの再現を試み、条件を切り分ける。agent-browser の新しい版で直っていないかも確かめる
- [ ] seed_login の section で認証が失われたことを検知する目印を決める (recipe が示す認証後のルートから外れた、など)。決まったら explorer の手順に入れる

## 関連

- ISSUE-109: 切り出し元。Basic 認証の section での検知はここで入れた
- ISSUE-40: Phase 1 の recipe の実行。seed_login の目印は recipe の書き方と関わる
