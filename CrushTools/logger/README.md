# CrowdWalk ロガー（エージェントログ・リンクログ）

CrowdWalk に 2 種類の CSV ログを追加するためのツールです。

| ログ | 1 行の単位 | 設定名（properties.json） |
|---|---|---|
| エージェントログ（`DynamicAgentLogger`） | 1 エージェント × 1 ステップ | `dynamic_logging` |
| リンクログ（`LinkAggregateLogger`） | 1 リンク × 1 ステップ（そのリンク上の全エージェントを集計） | `link_logging` |

どの値を記録するかは properties.json の `fields` で選びます。値そのものは Ruby 側の `TelemetryHandler.rb` が各エージェントに書き込みます。項目を増やしたいときも Ruby と properties.json を書き換えるだけで、Java の再ビルドは要りません。

このフォルダの中身:

- `patchLogger.py`: CrowdWalk の Java ソースにロガーを追加し、ビルドするスクリプト
- `TelemetryHandler.rb`: 記録する値をエージェントに書き込む Ruby モジュール
- `README.md`: この説明

## 1. 必要なもの

- Java JDK 17。CrowdWalk の Gradle は新しすぎる Java だとビルドに失敗することがあります。17 で動作を確認しています。
- Python 3（3.8 以上ならおそらく問題ありません）
- Git
- Windows の場合は Git Bash。CrowdWalk の起動スクリプト `quickstart.sh` は bash 用です。

Java が複数入っているときは、ビルドの前に JDK 17 を指定してください。

```bash
# Windows (Git Bash) の例。インストール先は自分の環境に合わせる
export JAVA_HOME="/c/Program Files/Java/jdk-17"
export PATH="$JAVA_HOME/bin:$PATH"
java -version      # 17 と表示されれば OK
```

## 2. インストール

### 2.1 CrowdWalk を取得する

```bash
git clone https://github.com/crest-cassia/CrowdWalk.git
cd CrowdWalk/crowdwalk
```

以降のコマンドは、すべてこの `crowdwalk/` フォルダ（`build.gradle` がある場所）で実行します。

### 2.2 このフォルダを置く

`crowdwalk/CrushTools/logger/` にこのフォルダをまるごとコピーします。

```
CrowdWalk/crowdwalk/
├── build.gradle
├── quickstart.sh
├── src/
└── CrushTools/
    └── logger/
        ├── patchLogger.py
        ├── TelemetryHandler.rb
        └── README.md
```

別の場所に置いても動きます。スクリプトは `build.gradle` を、まず今いるフォルダの周りで、次にスクリプト自身の上の階層で探します。

### 2.3 パッチを当ててビルドする

```bash
python3 CrushTools/logger/patchLogger.py
```

Windows ではコマンドが `python` の場合もあります。成功すると最後に次の行が出ます。

```
[✓] CrowdWalk patched and compiled successfully!
```

- ビルドせずにパッチだけ当てたいときは `--no-build` を付けます。あとで自分で `./gradlew compileJava jar -x test -x check` を実行すれば同じです。
- 2 回実行しても壊れません。2 回目は `[*] Already patched` と表示され、何も変わりません。
- `[!] Warning: Anchor missing` が出たら、CrowdWalk のバージョンが想定と違います（目印にしている行が見つからない）。その場合は先輩に相談してください。

変更されるファイル:

- 新規: `src/main/java/nodagumi/ananPJ/Simulator/DynamicAgentLogger.java`, `LinkAggregateLogger.java`
- 追記: 同じフォルダの `AgentHandler.java` に 8 行（ロガーの作成、毎ステップの記録、開始と終了）

元に戻すときは、`git checkout -- src` で Java ソースを戻し、新規の 2 ファイルを削除します。

## 3. シナリオの設定

ここでは、自分のシナリオフォルダ（例: `my_scenario/`）に次のファイルがある前提で説明します。

```
my_scenario/
├── properties.json
├── map.xml
├── gen.json
├── scenario.json
├── LoggedAgent.rb          ← 3.1 で作る
└── TelemetryHandler.rb     ← このフォルダからコピー
```

### 3.1 Ruby エージェントを作る

ログを取るには、毎ステップ `TelemetryHandler.update_telemetry` を呼ぶ Ruby エージェントが必要です。CrowdWalk 標準の歩き方をそのまま使い、値だけを記録する最小の例です。

`my_scenario/LoggedAgent.rb`:

```ruby
require 'RubyAgentBase.rb'
require 'TelemetryHandler.rb'

class LoggedAgent < RubyAgentBase
  # 毎ステップ呼ばれるメソッド。calcSpeed だけにしておくと速い
  TriggerFilter = ["calcSpeed"]

  def calcSpeed(previousSpeed)
    speed = super(previousSpeed)   # CrowdWalk 標準の速度計算

    TelemetryHandler.update_telemetry(@javaAgent, {
      speed:          speed,
      empty_speed:    getEmptySpeed(),
      link_id:        getCurrentLinkId(),
      position:       @javaAgent.getPositionOnLink(),
      link_direction: @javaAgent.isForwardDirection() ? 1 : -1,
    }, getCurrentTime())

    return speed
  end
end
```

注意点が 2 つあります。

- 最後の引数 `getCurrentTime()` は省略しないでください。エージェントログは、そのステップに更新されたエージェントだけを書き出します。時刻がないと、どのステップにも書き出されません。
- `speed` は必ず渡してください。渡さないと速度が 9999 として記録されます。

### 3.2 gen.json でエージェントを指定する

`agentType` を Ruby エージェントにして、クラス名を指定します。

```
#{ "version" : 2}
[
  {"rule":"EACH", "agentType":{"className":"RubyAgent", "rubyAgentClass":"LoggedAgent"},
   "startPlace":"START", "goal":"GOAL", "startTime":"18:00:00", "duration":60, "total":100}
]
```

`rule`、`startPlace`、`goal` などは自分のシナリオに合わせてください。大事なのは `className` と `rubyAgentClass` の 2 つです。

### 3.3 properties.json を設定する

```json
{
    "map_file": "map.xml",
    "generation_file": "gen.json",
    "scenario_file": "scenario.json",

    "use_ruby": true,
    "ruby_load_path": ".",
    "ruby_init_script": ["require 'LoggedAgent.rb'"],

    "dynamic_logging": {
        "file": "logs/agents.csv",
        "fields": ["link_id", "position", "current_speed", "link_direction"]
    },
    "link_logging": {
        "file": "logs/linkMetrics.csv",
        "fields": ["speed_fwd", "speed_bwd"]
    }
}
```

- `ruby_load_path`: `LoggedAgent.rb` と `TelemetryHandler.rb` を置いたフォルダ。`"."` は properties.json と同じフォルダです。
- `dynamic_logging` と `link_logging` は片方だけでも構いません。書かなかったほうのログは作られません。
- `file` のフォルダがなければ自動で作られます。

### 3.4 実行する

```bash
export LANG=ja_JP.UTF-8
export JAVA_OPTS='-Dgroovy.source.encoding=UTF-8 -Dfile.encoding=UTF-8'
sh quickstart.sh my_scenario/properties.json -c
```

`-c` は GUI なしで実行します。GUI で見ながら実行するなら `-g2`（2D）か `-g`（3D）です。

## 4. 記録できる項目

`TelemetryHandler.rb` が書き込む項目です。`fields` には、この中から必要なものだけを並べます。

| 項目名 | 内容 | `update_telemetry` に渡すキー |
|---|---|---|
| `link_id` | 今いるリンクの ID | `link_id` |
| `position` | リンク上の位置（m） | `position` |
| `link_direction` | 進行方向（1 = リンクの from→to、-1 = 逆向き） | `link_direction` |
| `current_speed` | 速度（m/s） | `speed` |
| `speed_fwd` / `speed_bwd` | 進行方向別の速度（もう一方の向きでは 0） | `speed`, `link_direction` |
| `empty_speed` | 混雑がないときの希望速度（m/s） | `empty_speed` |
| `agent_status` | 1 = `crushed` タグあり、0 = なし | （自動） |
| `last_telemetry_tick` | 最後に記録したステップ | （自動、`fields` には不要） |

CrushWalk（圧迫の物理モデル）用の項目もあります。`compression_pressure`、`raw_pressure`、`time_over_threshold`、`push_force`、`social_force`、`blocked_by` です。通常の CrowdWalk エージェントでは意味がないので、`fields` に入れないでください。特に `compression_pressure` は、値を渡さないと 9999 になります。

リンクログには、`fields` とは別に次の列が必ず入ります。

| 列 | 内容 |
|---|---|
| `current_traveling_period` | ステップ（秒） |
| `link_id` | リンク ID |
| `agent_count` | そのリンク上のエージェント数 |
| `mean_speed`, `std_speed` | 速度の平均と標準偏差 |
| `mean_<項目>`, `max_<項目>` | `fields` の各項目の平均と最大（最大は絶対値の最大） |
| `crush_now` | `crushed` タグのエージェントがいれば 1 |
| `queue_count` | ほぼ止まっている（速度 0.02 m/s 未満）エージェントの数 |

エージェントログの列は `current_traveling_period, generated_time, agent_id` と、`fields` に書いた項目です。

## 5. ログの項目を増やす

自分で計算した値（例: 周りの人数、ゴールまでの距離）を記録する手順です。Java の再ビルドは要りません。

手順 1: エージェントの中で、`update_telemetry` を呼んだ**あと**に値を書き込みます。

```ruby
    TelemetryHandler.update_telemetry(@javaAgent, { ... }, getCurrentTime())

    # 自分の項目。値は文字列にして入れる
    @javaAgent.config.setArg("dist_to_goal", my_distance.round(2).to_s)
```

`update_telemetry` より前に書くと、初回のステップで消えてしまいます。初回に、エージェントごとの記録用 config を作り直すためです。

手順 2: properties.json の `fields` に項目名を追加します。

```json
"fields": ["link_id", "current_speed", "dist_to_goal"]
```

注意:

- リンクログは数値しか集計できません。文字列は 0 として扱われます。
- エージェントログは値をそのまま書きます（カンマは `;` に置き換わります）。
- 記録されていない項目は、エージェントログでは `0` になります。

毎回同じ項目を使うなら、`TelemetryHandler.rb` の中に `config.setArg(...)` の行を 1 行足して、`update_telemetry` のキーで渡せるようにしても構いません。

## 6. うまくいかないとき

| 症状 | よくある原因 |
|---|---|
| CSV がヘッダーだけ | `update_telemetry` が呼ばれていない。`TriggerFilter` に `calcSpeed` がある？ `gen.json` の `rubyAgentClass` は正しい？ |
| エージェントログが空で、リンクログには行がある | `update_telemetry` の 3 つ目の引数（`getCurrentTime()`）がない |
| 全員が同じ値になっている | 古い `TelemetryHandler.rb` を使っている。このフォルダのもの（エージェントごとに config を分ける版）を使う |
| `cannot load such file -- TelemetryHandler.rb` | `ruby_load_path` に `TelemetryHandler.rb` を置いたフォルダが入っていない |
| ビルドが失敗する | Java のバージョン。`java -version` で 17 か確認する |
| `Anchor missing` | CrowdWalk のバージョン違い。2.3 を参照 |

## 7. 参考

- 元になったプロジェクト: CrushWalk（CrowdWalk に群衆圧迫の物理モデルを足したもの）。同じロガーを使っています。全部入りのパッチは `CrushTools/crush/patchCrowd.py` です。
- CrowdWalk 本体の Ruby エージェントの書き方は `src/main/ruby/RubyAgentBase.rb` の先頭のコメントにあります。
