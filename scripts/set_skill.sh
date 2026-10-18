#!/bin/bash
# 使い方: source /workspace/pai_kitchen/scripts/set_skill.sh <skill> [steps]
#   pai_training/configs/orig（講義の元設定）からスキル用の設定3ファイルを作り直す
S=${1:?skill name (bow, stir, ...)}
STEPS=${2:-3000}
PT=${PAI_TRAINING:-$HOME/pai_training}
cp "$PT"/configs/orig/conversion_config.yaml "$PT"/configs/orig/crane_features.py "$PT"/configs/orig/train_config.yaml "$PT"/configs/
sed -i "s#/home/ubuntu/dataset/rosbags/pekori#/workspace/data/rosbags/$S#; s#/home/ubuntu/dataset/lerobot_dataset/crane_plus_pekori#/workspace/data/lerobot/kitchen_$S#" "$PT"/configs/conversion_config.yaml
sed -i "s#crane_plus_pekori#kitchen_$S#; s#/home/ubuntu/dataset/lerobot_dataset/#/workspace/data/lerobot/#; s#task_name = \"pekori\"#task_name = \"$S\"#" "$PT"/configs/crane_features.py
sed -i "s#/home/ubuntu/dataset/lerobot_dataset/crane_plus_pekori#/workspace/data/lerobot/kitchen_$S#; s#\"crane_plus_pekori\"#\"kitchen_$S\"#; s#/home/ubuntu/checkpoints/train/crane_plus_pekori_act#/workspace/data/checkpoints/kitchen_${S}_act#" "$PT"/configs/train_config.yaml
sed -i "s/^training_steps: .*/training_steps: $STEPS/; s/^batch_size: .*/batch_size: 16/; s/^log_freq: .*/log_freq: 100/; s/^n_action_steps: .*/n_action_steps: 8/; s/action: \[-0.1, .*\]/action: [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5]/" "$PT"/configs/train_config.yaml
echo "== skill: $S  steps: $STEPS"
grep -hn "rosbags\|kitchen_\|task_name\|training_steps\|batch_size\|n_action_steps\|action:" "$PT"/configs/conversion_config.yaml "$PT"/configs/crane_features.py "$PT"/configs/train_config.yaml
