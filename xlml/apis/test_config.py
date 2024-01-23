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

"""Config file for a test job.

Note on dataclasses:

Why not use data classes? Before Python 3.10, init parameters for inherited
data classes are put in the order in which they are defined. Take this example:

```
class TestConfig(abc.ABC, Generic[A]):
  accelerator: A
  task_owner: Optional[str] = None

class TpuVmTest(TestConfig[Tpu]):
  test_name: str
```

`TpuVmTest.__init__`'s signature will be something like this:

```
def __init__(self, accelerator, task_owner=None, test_name):
  ...
```

Putting required positional arguments after required ones is, of course, not
allowed. This prevents us from defining any optional parameters on the parent
class. Python 3.10 adds keyword-only dataclass fields, but we have to get that
functionality from `attrs` for now.

When Composer updates to a recent Python version, we can use dataclasses.
"""

import abc
import attrs
from dags.vm_resource import TpuVersion
import json
import os
import shlex
from typing import Any, Generic, Iterable, List, Optional, TypeVar


class Accelerator(abc.ABC):
  """Represents an ML accelerator."""

  @property
  @abc.abstractmethod
  def name(self) -> str:
    """Name of this ML accelerator."""
    raise NotImplementedError


@attrs.define
class Tpu(Accelerator):
  """Represents a single Cloud TPU instance.

  Attributes:
    version: TPU device version.
    cores: Physical cores in this TPU type, i.e. the number of cores in the
      name.
    runtime_version: Runtime image version.
    network: The network that a TPU will be a part of.
    subnetwork: The subnetwork that a TPU will be a part of.
    reserved: The flag to define if a TPU is a Cloud reservation.
  """

  version: TpuVersion
  cores: int
  runtime_version: Optional[str] = None
  network: str = 'default'
  subnetwork: str = 'default'
  reserved: bool = False

  @property
  def name(self):
    """Name of this TPU type in the Cloud TPU API (e.g. 'v4-8')."""
    return f'v{self.version.value}-{self.cores}'


@attrs.define
class Gpu(Accelerator):
  """Represents a single Cloud GPU instance.

  Attributes:
    machine_type: The host type of the GPU. E.g., `a2-highgpu-1g`.
    image_family: Family of the image.
    count: Number of the GPU devices.
    accelerator_type: Type of the accelerator. E.g., `nvidia-test-v100`.
    runtime_version: Runtime image version.
  """

  machine_type: str
  image_family: str
  count: int
  accelerator_type: str
  runtime_version: str

  @property
  def name(self):
    """Name of this GPU type in the Cloud GPU API (e.g. 'a2-highgpu-1g')."""
    return self.accelerator_type


A = TypeVar('A', bound=Accelerator)


@attrs.define
class TestConfig(abc.ABC, Generic[A]):
  """Base class for end-to-end test configurations.

  Attributes:
    accelerator: Accelerator type required for this test.
    time_out_in_min: Test timeout in minutes.
    task_owner: Task owner username or link.
  """

  accelerator: A
  time_out_in_min: Optional[int] = attrs.field(default=None, kw_only=True)
  task_owner: str = attrs.field(default='unowned', kw_only=True)

  @property
  @abc.abstractmethod
  def benchmark_id(self) -> str:
    """Unique key for metrics generated by this test."""
    ...

  @property
  def setup_script(self) -> Optional[str]:
    """Optional script to run once when the accelerator is created."""
    return None

  @property
  @abc.abstractmethod
  def test_script(self) -> str:
    """Script to run on accelerator machine.

    The exit code of this script will be the test result.
    """
    ...


@attrs.define
class TpuVmTest(TestConfig[Tpu]):
  """Test config that runs on a single Cloud TPU VM instance.

  Attributes:
    test_name: Unique name for this test/model.
    set_up_cmds: List of commands to run once when TPU is created.
    run_model_cmds: List of commands to run the model under test.
    num_slices: Number of TPU slices.
    use_startup_script: If true, use startup script in GCE.
  """

  test_name: str
  set_up_cmds: Iterable[str]
  run_model_cmds: Iterable[str]
  num_slices: int = attrs.field(default=1, kw_only=True)
  use_startup_script: bool = attrs.field(default=False, kw_only=True)

  @property
  def benchmark_id(self) -> str:
    return (
        f'{self.test_name}-{self.accelerator.name}'
        if self.num_slices == 1
        else f'{self.test_name}-{self.num_slices}x{self.accelerator.name}'
    )

  @property
  def setup_script(self) -> Optional[str]:
    return '\n'.join(self.set_up_cmds)

  @property
  def test_script(self) -> str:
    return '\n'.join(self.run_model_cmds)

  @property
  def startup_script(self) -> str:
    if self.use_startup_script == False:
      return ''

    main_command = '\n'.join(self.set_up_cmds + self.run_model_cmds)
    final_command = f"""
bash -c '{main_command} 2>&1 | tee /tmp/logs &
pid=$!
echo $pid > /tmp/main_process_id.txt
wait $pid
exit_status=$?
echo $exit_status > /tmp/process_exit_status.txt'
"""
    return final_command


@attrs.define
class GpuVmTest(TestConfig[Gpu]):
  """Test config that runs on a single Cloud GPU VM instance.

  Attributes:
    test_name: Unique name for this test/model.
    set_up_cmds: List of commands to run once when GPU is created.
    run_model_cmds: List of commands to run the model under test.
  """

  test_name: str
  set_up_cmds: Iterable[str]
  run_model_cmds: Iterable[str]

  @property
  def benchmark_id(self) -> str:
    return f'{self.test_name}-{self.accelerator.name}'

  @property
  def setup_script(self) -> Optional[str]:
    return '\n'.join(self.set_up_cmds)

  @property
  def test_script(self) -> str:
    return '\n'.join(self.run_model_cmds)


@attrs.define
class TpuGkeTest(TestConfig[Tpu]):
  """Test config that runs on a single Cloud TPU instance in GKE cluster.

  Attributes:
    test_name: Unique name for this test/model.
    cluster_name: Name of the cluster that has provisioned TPUs.
    docker_image: Image of the docker to run.
    set_up_cmds: List of commands to run once when TPU is created.
    run_model_cmds: List of commands to run the model under test.
    startup_time_out_in_sec: Timeout to start up the pod.
    num_slices: Number of TPU slices.
  """

  test_name: str
  cluster_name: str
  docker_image: str
  set_up_cmds: Iterable[str]
  run_model_cmds: Iterable[str]
  startup_time_out_in_sec: int = attrs.field(default=300, kw_only=True)
  num_slices: int = attrs.field(default=1, kw_only=True)

  @property
  def benchmark_id(self) -> str:
    return (
        f'{self.test_name}-{self.accelerator.name}'
        if self.num_slices == 1
        else f'{self.test_name}-{self.num_slices}x{self.accelerator.name}'
    )

  @property
  def setup_script(self) -> Optional[str]:
    return ';'.join(self.set_up_cmds)

  @property
  def test_script(self) -> str:
    return ';'.join(self.run_model_cmds)


@attrs.define
class JSonnetTpuVmTest(TestConfig[Tpu]):
  """Convert legacy JSonnet test configs into a Task.

  Do not construct directly. Instead, use the `from_*` factory functions which
  parse pre-compiled JSonnet test configs.

  Attributes:
    test_name: Unique name of this test/model.
    setup: Multi-line script that configures the TPU instance.
    exports: Extra setup commands to run in same shell as test_command.
    test_command: Command and arguments to execute on the TPU VM.
    num_slices: Number of TPU slices.
    use_startup_script: If true, use startup script in GCE.
  """

  test_name: str
  setup: str
  exports: str
  test_command: List[str]
  num_slices: int = 1
  
  # We need to add `use_startup_script` here since `JSonnetTpuVmTest` and `TpuVmTest` both use `TpuQueuedResourceTask`
  use_startup_script: bool = attrs.field(default=False, kw_only=True)

  @staticmethod
  def _load_compiled_jsonnet(test_name: str) -> Any:
    # TODO(wcromar): Parse GPU tests too
    config_dir = os.environ.get(
        'XLMLTEST_CONFIGS', '/home/airflow/gcs/dags/dags/jsonnet'
    )
    test_path = os.path.join(config_dir, test_name)
    with open(test_path, 'r') as f:
      test = json.load(f)

    return test

  @staticmethod
  def _from_json_helper(
      test: Any,
      setup: str,
      exports: str,
      test_command: List[str],
      reserved: bool,
  ):
    return JSonnetTpuVmTest(
        test_name=test['testName'],
        accelerator=Tpu(
            version=TpuVersion(str(test['accelerator']['version'])),
            cores=test['accelerator']['size'],
            runtime_version=test['tpuSettings']['softwareVersion'],
            reserved=reserved,
        ),
        setup=setup,
        exports=exports,
        test_command=test_command,
        # `timeout` is in seconds
        time_out_in_min=test['timeout'] // 60,
    )

  @staticmethod
  def from_jax(test_name: str, reserved_tpu: bool = True):
    """Parses a compiled legacy JSonnet config test from `tests/jax`."""
    test = JSonnetTpuVmTest._load_compiled_jsonnet(test_name)
    return JSonnetTpuVmTest._from_json_helper(
        test,
        # TODO(wcromar): make this less hacky
        setup=test['setup'],
        exports='',
        test_command=['bash', '-c', test['runTest']],
        reserved=reserved_tpu,
    )

  @staticmethod
  def from_pytorch(test_name: str, reserved_tpu: bool = True):
    """Parses a compiled legacy JSonnet test config from `tests/pytorch`."""
    test = JSonnetTpuVmTest._load_compiled_jsonnet(test_name)
    return JSonnetTpuVmTest._from_json_helper(
        test,
        setup=test['tpuSettings']['tpuVmPytorchSetup']
        # HACK: Extra setup assumes a new shell in home directory
        + '\ncd ~\n' + test['tpuSettings']['tpuVmExtraSetup'],
        exports=test['tpuSettings']['tpuVmExports'],
        test_command=test['command'],
        reserved=reserved_tpu,
    )

  @property
  def benchmark_id(self) -> str:
    return self.test_name

  @property
  def setup_script(self) -> Optional[str]:
    return '\n'.join(['set -xue', self.setup])

  # TODO(wcromar): replace configmaps
  @property
  def test_script(self) -> str:
    return '\n'.join([
        'set -xue',
        self.exports,
        ' '.join(shlex.quote(s) for s in self.test_command),
    ])

  # We need to return None here since `JSonnetTpuVmTest` and `TpuVmTest` both use `TpuQueuedResourceTask`
  @property
  def startup_script(self) -> str:
    return None
