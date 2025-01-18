from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional, Type

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
	AIMessage,
	BaseMessage,
	HumanMessage,
	SystemMessage,
	ToolMessage,
)
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from browser_use.agent.message_manager.views import MessageHistory, MessageMetadata
from browser_use.agent.prompts import AgentMessagePrompt, SystemPrompt
from browser_use.agent.views import ActionResult, AgentOutput, AgentStepInfo
from browser_use.browser.views import BrowserState

logger = logging.getLogger(__name__)


class MessageManager:
	def __init__(
		self,
		llm: BaseChatModel,
		task: str,
		action_descriptions: str,
		system_prompt_class: Type[SystemPrompt],
		max_input_tokens: int = 128000,
		estimated_tokens_per_character: int = 3,
		image_tokens: int = 800,
		include_attributes: list[str] = [],
		max_error_length: int = 400,
		max_actions_per_step: int = 10,
		use_vision: bool = True,
	):
		self.llm = llm
		self.use_vision = use_vision
		self.system_prompt_class = system_prompt_class
		self.max_input_tokens = max_input_tokens
		self.history = MessageHistory()
		self.task = task
		self.action_descriptions = action_descriptions
		self.ESTIMATED_TOKENS_PER_CHARACTER = estimated_tokens_per_character
		self.IMG_TOKENS = image_tokens
		self.include_attributes = include_attributes
		self.max_error_length = max_error_length

		system_message = self.system_prompt_class(
			self.action_descriptions,
			current_date=datetime.now(),
			max_actions_per_step=max_actions_per_step,
		).get_system_message()

		self.id = 1

		self.tool_call_example = [
			{
				'name': 'AgentOutput',
				'args': {
					'current_state': {
						'evaluation_previous_goal': 'Unknown - no previous action',
						'next_goal': 'Open the browser to start with the task',
						'completed_subtask': 'Started task',
						'todo_subtasks': 'Break down the state and task into clear subtasks',
						'confidence': 100,
					},
					'action': [{'go_to_url': 'blank'}],
				},
				'id': str(self.id),
				'type': 'tool_call',
			}
		]

		# tool_call = AIMessage(content='', tool_call_id='1', tool_calls=tool_call_example)
		# tool_answer = ToolMessage(content='', tool_call_id='1')
		self.last_output = AIMessage(content='', tool_calls=self.tool_call_example)
		self.prompt = [
			SystemMessage(content=system_message),
			HumanMessage(content=self.task_instructions(task)),
		]
		self.past_states = []

	@staticmethod
	def task_instructions(task: str) -> str:
		content = f'Your ultimate task is: {task}. If you achieved your ultimate task, stop everything and use the done action in the next step to complete the task. If not, continue as usual.'
		return content

	def get_messages(
		self,
		state: BrowserState,
		result: Optional[List[ActionResult]] = None,
		step_info: Optional[AgentStepInfo] = None,
	) -> list[BaseMessage]:
		# Format the result and memory state as a string
		browser_state = AgentMessagePrompt(
			state,
			result,
			include_attributes=self.include_attributes,
			max_error_length=self.max_error_length,
			step_info=step_info,
		)

		# Get the base messages from the prompt template
		messages = []

		# 1. Add system message and task message (these are already in the prompt template)
		messages.extend(self.prompt)

		# 2. Add summaries except the last one because it is the current model output
		for i, past_state in enumerate(self.past_states[:-1]):
			messages.append(HumanMessage(content=f'Step {i + 1} state: {past_state}'))

		# 3. Add AI message (last output)
		messages.append(self.last_output)

		# 4. Add tool message with result of the tool call
		messages.append(
			ToolMessage(
				content=browser_state.get_result_and_error_description(),
				tool_call_id=str(self.id),
			)
		)

		# 5. Add human message with screenshot if using vision
		human_msg = (
			'This is the current page, give me the next action to reach my ultimate goal: \n'
			+ browser_state.get_state_description()
		)
		if self.use_vision and state.screenshot:
			messages.append(
				HumanMessage(
					content=[
						{
							'type': 'text',
							'text': human_msg,
						},
						{
							'type': 'image_url',
							'image_url': {'url': f'data:image/png;base64,{state.screenshot}'},
						},
					]
				)
			)
		else:  # no vision
			messages.append(
				HumanMessage(
					content=human_msg,
				)
			)

		return messages

	def set_last_output(self, model_output: AgentOutput):
		self.id += 1

		tool_calls = [
			{
				'name': 'AgentOutput',
				'args': model_output.model_dump(exclude_none=True),
				'id': str(self.id),
				'type': 'tool_call',
			}
		]

		# Create new AI message with the tool calls
		self.last_output = AIMessage(
			content='',
			tool_calls=tool_calls,
		)
		self.past_states.append(model_output.current_state.model_dump())
