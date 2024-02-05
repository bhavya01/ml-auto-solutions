# Copyright 2023 Google LLC
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

"""A DAG to run all GKE examples."""

import datetime
from airflow import models
from dags.vm_resource import TpuVersion, Project, Zone, ClusterName, DockerImage
from dags.examples.configs import xpk_example_config as config
from xlml.apis import task


# TODO(ranran): add following examples:
# 1) jax_resnet_tpu_qr (diff dag)
# 2) jax_vit_tpu_qr_benchmark (diff dag)
# 3) jax_vit_tpu_xpk_benchmark (same dag)
with models.DAG(
    dag_id="gke_example_dag",
    schedule=None,
    tags=["example", "gke", "xlml", "benchmark"],
    start_date=datetime.datetime(2023, 11, 29),
    catchup=False,
) as dag:
  # DockerBuildTask will run a cloud build in the input directory. Since the
  # `custom_build` flag is unspecified, cloud build will run a Docker build
  # using the Dockerfile in that path.
  docker_image_build = task.DockerBuildTask(
      build_dir="dags/examples/docker/docker_build",
      image_name=DockerImage.XPK_JAX_TEST.value,
  ).run()
  flax_resnet_tpu_singleslice_v4_8 = config.get_flax_resnet_xpk_config(
      tpu_version=TpuVersion.V4,
      tpu_cores=8,
      tpu_zone=Zone.US_CENTRAL2_B.value,
      test_name="resnet-single-slice",
      project_name=Project.CLOUD_ML_AUTO_SOLUTIONS.value,
      cluster_name=ClusterName.V4_8_CLUSTER.value,
      docker_image=docker_image_build.output,
      time_out_in_min=60,
  ).run()

  # Multiple docker builds can be part of the same DAG by overriding `task_id`.
  # With `custom_build=True`, the DockerBuildTask will use the cloudbuild.yaml
  # to build the image.
  custom_docker_image_build = task.DockerBuildTask(
      build_dir="dags/examples/docker/custom_build",
      image_name=DockerImage.XPK_JAX_TEST.value,
      task_id="custom_docker_build",
      custom_build=True,
  ).run()
  flax_resnet_tpu_multislice_v4_128 = config.get_flax_resnet_xpk_config(
      tpu_version=TpuVersion.V4,
      tpu_cores=128,
      tpu_zone=Zone.US_CENTRAL2_B.value,
      test_name="resnet-multi-slice",
      project_name=Project.TPU_PROD_ENV_MULTIPOD.value,
      cluster_name=ClusterName.V4_128_MULTISLICE_CLUSTER.value,
      docker_image=custom_docker_image_build.output,
      time_out_in_min=60,
      num_slices=4,
  ).run()
