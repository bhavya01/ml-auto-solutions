# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utilities to construct configs for maxdiffusion inference DAG."""

import datetime
import json
from typing import Dict
from xlml.apis import gcp_config, metric_config, task, test_config
from dags import test_owner
from dags.multipod.configs import common
from dags.vm_resource import TpuVersion, Project, RuntimeVersion

PROJECT_NAME = Project.CLOUD_ML_AUTO_SOLUTIONS.value
RUNTIME_IMAGE = RuntimeVersion.TPU_UBUNTU2204_BASE.value
GCS_SUBFOLDER_PREFIX = test_owner.Team.INFERENCE.value


def _modify_save_metrics(metrics_file, model_configs):
  metrics = json.loads(metrics_file)
  for k, v in model_configs:
    metrics["dimensions"][k] = str(v)
  with open(metrics_file, "w") as f:
    f.write(json.dumps(metrics))


def get_maxdiffusion_inference_nightly_config(
    tpu_version: TpuVersion,
    tpu_cores: int,
    tpu_zone: str,
    time_out_in_min: int,
    test_name: str,
    test_mode: common.SetupMode,
    project_name: str = PROJECT_NAME,
    runtime_version: str = RUNTIME_IMAGE,
    network: str = "default",
    subnetwork: str = "default",
    is_tpu_reserved: bool = True,
    num_slices: int = 1,
    model_configs: Dict = {},
) -> task.TpuQueuedResourceTask:
  job_gcp_config = gcp_config.GCPConfig(
      project_name=project_name,
      zone=tpu_zone,
      dataset_name=metric_config.DatasetOption.BENCHMARK_DATASET,
  )

  per_device_bat_size = model_configs["per_device_batch_size"]
  attention = model_configs["attention"]
  model_name = model_configs["model_name"]
  set_up_cmds = (
      "pip install --upgrade pip",
      # Download maxdiffusion
      "git clone https://github.com/google/maxdiffusion.git"
      # Create a python virtual environment
      "sudo apt-get -y update",
      "sudo apt-get -y install python3.10-venv",
      "python -m venv .env",
      "source .env/bin/activate",
      # Setup Maxdiffusion
      "cd maxdiffusion",
      "pip3 install jax[tpu] -f https://storage.googleapis.com/jax-releases/libtpu_releases.html",
      "pip3 install -r requirements.txt",
      "pip3 install .",
      # dependency for controlnet
      "apt-get install ffmpeg libsm6 libxext6  -y" "cd ..",
  )

  if model_name == "SDXL-Base-1.0":
    run_model_cmds = (
        # Start virtual environment
        "source .env/bin/activate",
        ### Benchmark
        "cd maxdiffusion",
        # Configure flags
        "cd .."
        f""" python -m src.maxdiffusion.generate_sdxl src/maxdiffusion/configs/base_xl.yml run_name="my_run" per_device_batch_size={per_device_bat_size} attention="{attention}" """,
        "cd ..",
        f"gsutil cp metrics.json {metric_config.SshEnvVars.GCS_OUTPUT.value}",
    )
  if model_name == "SDXL-Lightning":
    run_model_cmds = (
        # Start virtual environment
        "source .env/bin/activate",
        ### Benchmark
        "cd maxdiffusion",
        # Configure flags
        "cd .."
        f""" python -m src.maxdiffusion.generate_sdxl src/maxdiffusion/configs/base_xl.yml run_name="my_run" lightning_repo="ByteDance/SDXL-Lightning" lightning_ckpt="sdxl_lightning_4step_unet.safetensors" per_device_batch_size={per_device_bat_size} attention="{attention}" """,
        "cd ..",
        f"gsutil cp metrics.json {metric_config.SshEnvVars.GCS_OUTPUT.value}",
    )
  if model_name == "SDXL-ControlNet":
    run_model_cmds = (
        # Start virtual environment
        "source .env/bin/activate",
        ### Benchmark
        "cd maxdiffusion",
        # Configure flags
        "cd .."
        f""" python src/maxdiffusion/controlnet/generate_controlnet_sdxl_replicated.py per_device_batch_size={per_device_bat_size} attention="{attention}" """,
        "cd ..",
        f"gsutil cp metrics.json {metric_config.SshEnvVars.GCS_OUTPUT.value}",
    )

  _modify_save_metrics("metrics.json", model_configs)
  job_test_config = test_config.TpuVmTest(
      test_config.Tpu(
          version=tpu_version,
          cores=tpu_cores,
          runtime_version=runtime_version,
          reserved=is_tpu_reserved,
          network=network,
          subnetwork=subnetwork,
      ),
      test_name=test_name,
      set_up_cmds=set_up_cmds,
      run_model_cmds=run_model_cmds,
      timeout=datetime.timedelta(minutes=time_out_in_min),
      task_owner=test_owner.VIJAYA_S,
      num_slices=num_slices,
      gcs_subfolder=f"{GCS_SUBFOLDER_PREFIX}/maxdiffusion",
  )

  job_metric_config = metric_config.MetricConfig(
      json_lines=metric_config.JSONLinesConfig("metrics.json"),
      use_runtime_generated_gcs_folder=True,
  )

  return task.TpuQueuedResourceTask(
      task_test_config=job_test_config,
      task_gcp_config=job_gcp_config,
      task_metric_config=job_metric_config,
  )
