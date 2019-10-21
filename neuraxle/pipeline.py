"""
Neuraxle's Pipeline Classes
====================================
This is the core of Neuraxle's pipelines. You can chain steps to call them one after an other.

..
    Copyright 2019, Neuraxio Inc.

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

"""
from abc import ABC, abstractmethod
from copy import copy
from typing import Any, Tuple, List

from neuraxle.base import BaseStep, TruncableSteps, NamedTupleList, ResumableStepMixin, DataContainer, NonFittableMixin, \
    Barrier, ExecutionContext, ExecutionMode
from neuraxle.checkpoints import Checkpoint

DEFAULT_CACHE_FOLDER = 'cache'


class BasePipeline(TruncableSteps, ABC):
    def __init__(self, steps: NamedTupleList):
        TruncableSteps.__init__(self, steps_as_tuple=steps)

    @abstractmethod
    def fit(self, data_inputs, expected_outputs=None) -> 'BasePipeline':
        raise NotImplementedError()

    @abstractmethod
    def transform(self, data_inputs):
        raise NotImplementedError()

    @abstractmethod
    def fit_transform(self, data_inputs, expected_outputs=None) -> ('BasePipeline', Any):
        raise NotImplementedError()


class Pipeline(BasePipeline):
    """
    Fits and transform steps
    """

    def __init__(self, steps: NamedTupleList, cache_folder=DEFAULT_CACHE_FOLDER):
        BasePipeline.__init__(self, steps=steps)
        self.cache_folder = cache_folder

    def transform(self, data_inputs: Any):
        """
        After loading the last checkpoint, transform each pipeline steps

        :param data_inputs: the data input to transform
        :return: transformed data inputs
        """
        current_ids = self.hash(
            current_ids=None,
            hyperparameters=self.hyperparams,
            data_inputs=data_inputs
        )
        data_container = DataContainer(current_ids=current_ids, data_inputs=data_inputs)

        context = ExecutionContext.create_from_root(self, ExecutionMode.TRANSFORM, self.cache_folder)

        data_container = self._transform_core(data_container, context)

        return data_container.data_inputs

    def fit_transform(self, data_inputs, expected_outputs=None) -> ('Pipeline', Any):
        """
        After loading the last checkpoint, fit transform each pipeline steps

        :param data_inputs: the data input to fit on
        :param expected_outputs: the expected data output to fit on
        :return: the pipeline itself
        """
        current_ids = self.hash(
            current_ids=None,
            hyperparameters=self.hyperparams,
            data_inputs=data_inputs
        )
        data_container = DataContainer(
            current_ids=current_ids,
            data_inputs=data_inputs,
            expected_outputs=expected_outputs
        )

        context = ExecutionContext.create_from_root(self, ExecutionMode.FIT_TRANSFORM, self.cache_folder)

        new_self, data_container = self._fit_transform_core(data_container, context)

        return new_self, data_container.data_inputs

    def fit(self, data_inputs, expected_outputs=None) -> 'Pipeline':
        """
        After loading the last checkpoint, fit each pipeline steps

        :param data_inputs: the data input to fit on
        :param expected_outputs: the expected data output to fit on
        :return: the pipeline itself
        """
        current_ids = self.hash(
            current_ids=None,
            hyperparameters=self.hyperparams,
            data_inputs=data_inputs
        )
        data_container = DataContainer(
            current_ids=current_ids,
            data_inputs=data_inputs,
            expected_outputs=expected_outputs
        )

        context = ExecutionContext.create_from_root(self, ExecutionMode.FIT, self.cache_folder)

        new_self, data_container = self._fit_core(data_container, context)

        return new_self

    def inverse_transform(self, processed_outputs) -> Any:
        """
        After transforming all data inputs, and obtaining a prediction, we can inverse transform the processed outputs

        :param processed_outputs: the forward transformed data input
        :return: backward transformed processed outputs
        """
        for step_name, step in list(reversed(self.items())):
            processed_outputs = step.inverse_transform(processed_outputs)
        return processed_outputs

    def handle_fit(self, data_container: DataContainer, context: ExecutionContext) -> (
            'BaseStep', DataContainer):
        """
        Fit transform then rehash ids with hyperparams and transformed data inputs

        :param data_container: data container to fit transform
        :param context: execution context
        :return: tuple(fitted pipeline, transformed data container)
        """
        new_self, data_container = self._fit_core(data_container, context)

        ids = self.hash(data_container.current_ids, self.hyperparams, data_container.data_inputs)
        data_container.set_current_ids(ids)

        return new_self, data_container

    def handle_fit_transform(self, data_container: DataContainer, context: ExecutionContext) -> (
            'BaseStep', DataContainer):
        """
        Fit transform then rehash ids with hyperparams and transformed data inputs

        :param data_container: data container to fit transform
        :param context: execution context
        :return: tuple(fitted pipeline, transformed data container)
        """
        new_self, data_container = self._fit_transform_core(data_container, context)

        ids = self.hash(data_container.current_ids, self.hyperparams, data_container.data_inputs)
        data_container.set_current_ids(ids)

        return new_self, data_container

    def handle_transform(self, data_container: DataContainer, context: ExecutionContext) -> DataContainer:
        """
        Transform then rehash ids with hyperparams and transformed data inputs

        :param data_container: data container to transform
        :param context: execution context
        :return: tuple(fitted pipeline, transformed data container)
        """
        data_container = self._transform_core(data_container, context)

        ids = self.hash(data_container.current_ids, self.hyperparams, data_container.data_inputs)
        data_container.set_current_ids(ids)

        return data_container

    def _fit_core(self, data_container: DataContainer, context: ExecutionContext) -> ('Pipeline', DataContainer):
        """
        After loading the last checkpoint, fit transform each pipeline steps,
        but only fit the last pipeline step.

        :param data_container: the data container to fit transform on
        :param context: execution context
        :return: tuple(pipeline, data_container)
        """
        steps_left_to_do, data_container = self._load_checkpoint(data_container, context)
        self.setup()

        index_last_step = len(steps_left_to_do) - 1

        new_steps_as_tuple: NamedTupleList = []

        for index, (step_name, step) in enumerate(steps_left_to_do):
            step.setup()
            sub_step_context = context.push(step)

            if index != index_last_step:
                step, data_container = step.handle_fit_transform(data_container, sub_step_context)
            else:
                step, data_container = step.handle_fit(data_container, sub_step_context)

            new_steps_as_tuple.append((step_name, step))

        self.steps_as_tuple = self.steps_as_tuple[
                              :len(self.steps_as_tuple) - len(steps_left_to_do)] + new_steps_as_tuple

        return self, data_container

    def _fit_transform_core(self, data_container: DataContainer, context: ExecutionContext) -> (
            'Pipeline', DataContainer):
        """
        After loading the last checkpoint, fit transform each pipeline steps

        :param data_container: the data container to fit transform on
        :param context: execution context
        :return: tuple(pipeline, data_container)
        """
        steps_left_to_do, data_container = self._load_checkpoint(data_container, context)
        self.setup()

        new_steps_as_tuple: NamedTupleList = []

        for step_name, step in steps_left_to_do:
            step.setup()
            sub_step_context = context.push(step)

            step, data_container = step.handle_fit_transform(data_container, sub_step_context)

            new_steps_as_tuple.append((step_name, step))

        self.steps_as_tuple = self.steps_as_tuple[:len(self.steps_as_tuple) - len(steps_left_to_do)] + \
                              new_steps_as_tuple

        return self, data_container

    def _transform_core(self, data_container: DataContainer, context: ExecutionContext) -> DataContainer:
        """
        After loading the last checkpoint, transform each pipeline steps

        :param data_container: the data container to transform
        :return: transformed data container
        """
        steps_left_to_do, data_container = self._load_checkpoint(data_container, context)
        self.setup()

        for step_name, step in steps_left_to_do:
            step.setup()
            sub_context = context.push(step)
            data_container = step.handle_transform(data_container, sub_context)

        return data_container

    def _load_checkpoint(self, data_container: DataContainer, context: ExecutionContext) -> \
            Tuple[NamedTupleList, DataContainer]:
        """
        Try loading a pipeline cache with the passed data container.
        If pipeline cache loading succeeds, find steps left to do,
        and load the latest data container.

        :param data_container: the data container to resume
        :param context: the execution context to resume
        :return: tuple(steps left to do, last checkpoint data container)
        """
        return self.steps_as_tuple, data_container


class ResumablePipeline(ResumableStepMixin, Pipeline):
    """
    Fits and transform steps after latest checkpoint
    """

    def _load_checkpoint(
            self,
            data_container: DataContainer,
            context: ExecutionContext
    ) -> Tuple[NamedTupleList, DataContainer]:
        """
        Try loading a pipeline cache with the passed data container.
        If pipeline cache loading succeeds, find steps left to do,
        and load the latest data container.

        :param data_container: the data container to resume
        :param context: the execution context to resume
        :return: tuple(steps left to do, last checkpoint data container)
        """
        new_starting_step_index, starting_step_data_container = \
            self._get_starting_step_info(data_container, context)

        loaded_pipeline = self.load(context)
        if not self.are_steps_before_index_the_same(loaded_pipeline, new_starting_step_index):
            return self.steps_as_tuple, data_container

        self._assign_loaded_pipeline_into_self(loaded_pipeline)

        step = self[new_starting_step_index]
        if isinstance(step, Checkpoint):
            context = context.push(step)
            starting_step_data_container = step.read_checkpoint(starting_step_data_container, context)

        return self[new_starting_step_index:], starting_step_data_container

    def _assign_loaded_pipeline_into_self(self, loaded_self):
        self.steps_as_tuple = loaded_self.steps_as_tuple
        self._refresh_steps()
        self.hyperparams = loaded_self.hyperparams
        self.hyperparams_space = loaded_self.hyperparams_space

    def _get_starting_step_info(self, data_container: DataContainer, context: ExecutionContext) -> Tuple[
        int, DataContainer]:
        """
        Find the index of the latest step that can be resumed

        :param data_container: the data container to resume
        :return: index of the latest resumable step, data container at starting step
        """
        starting_step_data_container = copy(data_container)
        starting_step_context = copy(context)
        current_data_container = copy(data_container)
        index_latest_checkpoint = 0

        for index, (step_name, step) in enumerate(self.items()):
            sub_step_context = starting_step_context.push(step)
            if isinstance(step, ResumableStepMixin) and step.should_resume(current_data_container, sub_step_context):
                index_latest_checkpoint = index
                starting_step_data_container = copy(current_data_container)

            current_ids = step.hash(
                current_ids=current_data_container.current_ids,
                hyperparameters=step.hyperparams,
                data_inputs=current_data_container.data_inputs
            )

            current_data_container.set_current_ids(current_ids)

        return index_latest_checkpoint, starting_step_data_container

    def should_resume(self, data_container: DataContainer, context: ExecutionContext) -> bool:
        """
        Return True if the pipeline has a saved checkpoint that it can resume from

        :param context: execution context
        :param data_container: the data container to resume
        :return: bool
        """
        for index, (step_name, step) in enumerate(reversed(self.items())):
            sub_step_context = context.push(step)
            if isinstance(step, ResumableStepMixin) and step.should_resume(data_container, sub_step_context):
                return True

        return False


class MiniBatchSequentialPipeline(NonFittableMixin, Pipeline):
    """
    Mini Batch Sequential Pipeline class to create a pipeline processing data inputs in batch.
    """

    def __init__(self, steps: NamedTupleList, batch_size):
        Pipeline.__init__(self, steps)
        self.batch_size = batch_size

    def transform(self, data_inputs: Any):
        """
        :param data_inputs: the data input to transform
        :return: transformed data inputs
        """
        current_ids = self._create_current_ids(data_inputs)
        data_container = DataContainer(current_ids=current_ids, data_inputs=data_inputs)
        context = ExecutionContext.create_from_root(self, ExecutionMode.TRANSFORM, self.cache_folder)
        data_container = self.handle_transform(data_container, context)

        return data_container.data_inputs

    def fit_transform(self, data_inputs, expected_outputs=None) -> ('Pipeline', Any):
        """
        :param data_inputs: the data input to fit on
        :param expected_outputs: the expected data output to fit on
        :return: the pipeline itself
        """
        current_ids = self._create_current_ids(data_inputs)
        data_container = DataContainer(
            current_ids=current_ids,
            data_inputs=data_inputs,
            expected_outputs=expected_outputs
        )
        context = ExecutionContext.create_from_root(self, ExecutionMode.FIT_TRANSFORM, self.cache_folder)
        new_self, data_container = self.handle_fit_transform(data_container, context)

        return new_self, data_container.data_inputs

    def _create_current_ids(self, data_inputs):
        current_ids = self.hash(
            current_ids=None,
            hyperparameters=self.hyperparams,
            data_inputs=data_inputs
        )
        return current_ids

    def handle_transform(self, data_container: DataContainer, context: ExecutionContext) -> DataContainer:
        """
        Transform all sub pipelines splitted by the Barrier steps.

        :param data_container: data container to transform.
        :param context: execution context
        :return: data container
        """
        sub_pipelines = self._create_sub_pipelines()

        for sub_pipeline in sub_pipelines:
            barrier = sub_pipeline[-1]
            data_container = barrier.join_transform(
                step=sub_pipeline,
                data_container=data_container,
                context=context
            )
            current_ids = self.hash(data_container.current_ids, self.hyperparams)
            data_container.set_current_ids(current_ids)

        return data_container

    def handle_fit_transform(self, data_container: DataContainer, context: ExecutionContext) -> \
            Tuple['MiniBatchSequentialPipeline', DataContainer]:
        """
        Transform all sub pipelines splitted by the Barrier steps.

        :param data_container: data container to transform.
        :param context: execution context
        :return: data container
        """
        sub_pipelines = self._create_sub_pipelines()
        index_start = 0

        for sub_pipeline in sub_pipelines:
            sub_context = context.push(sub_pipeline)
            sub_pipeline.setup()

            barrier = sub_pipeline[-1]
            sub_pipeline, data_container = barrier.join_fit_transform(
                step=sub_pipeline,
                data_container=data_container,
                context=sub_context
            )
            current_ids = self.hash(data_container.current_ids, self.hyperparams)
            data_container.set_current_ids(current_ids)

            new_self = self[:index_start] + sub_pipeline
            if index_start + len(sub_pipeline) < len(self):
                new_self += self[index_start + len(sub_pipeline):]

            self.steps_as_tuple = new_self.steps_as_tuple
            index_start += len(sub_pipeline)

        return self, data_container

    def _create_sub_pipelines(self) -> List['MiniBatchSequentialPipeline']:
        """
        Create sub pipelines by splitting the steps by the join type name.

        :return: list of sub pipelines
        """
        sub_pipelines: List[MiniBatchSequentialPipeline] = self.split(Barrier)
        for sub_pipeline in sub_pipelines:
            if not sub_pipeline.ends_with(Barrier):
                raise Exception(
                    'At least one Barrier step needs to be at the end of a streaming pipeline.'
                )

        return sub_pipelines
