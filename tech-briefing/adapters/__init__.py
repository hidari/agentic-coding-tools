"""本体の Protocol を実物のライブラリと通信で満たすアダプタ。

本体は import しない。入口のスクリプトは関数の中でだけ読む。テストが読めるのは実行時の依存を import しない
アダプタだけ (tests/test_boundaries.py が固定する)。
"""
