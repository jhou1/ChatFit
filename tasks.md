# Agents
## supervisor_agent
- [x] graph to conditionally call the subagents
- [x] add the ability for casual chat
- [x] bug: fix the slowness of supervisor_agent
  - [x] system prompt, conditional edges
  - [x] support using proxy
  - [x] minimize llm calls to decrease the chances of using proxy
- [x] have memory of user preference
- [ ] Long term memory
- [ ] compacting memory on condition
- [ ] I could develop an mcp service to export my training to google spreadsheet, and use it to visualize trainings.

## Training
- [x] add tool to expand acronym
- [ ] add tool to retrospect training history using time range provided by user
  - [x] add tool to retrieve past n days of trainings
- [ ] Give training agent knowledge for strength development and nutrition, fat loss.
- [ ] if I record wrong data, I should be able to modify it, via chat.
    - [ ] when updating, the agent should show the data for the user to confirm before updating. Because user could be entering multiple records in one day

## Meal
- [ ] ambitious: add tool to analyze nutrition
- [ ] add tool to retrospect meal history using time range provided by user.
- [ ] Add system to suggest what to eat
- [ ] add RAG system to use my recipes

## Understanding user proference: Memory, VectoreStore, RAG
- [x] short term memory using state
- [ ] long term memory using store

# UX
- [x] basic chat interface, using rich
- [-] 解决terminal输入过程中，中文占用字符比英文宽的问题。如果输入了中文，删除时止退了一格，但中文占用了两格，所以会留下一个空格。
- [x] Agent serves over api so I can use it on my phone. -- solved with telegram bot
- [x] /clear command to clear agent context

## API
- [ ] make RAG path configurable

## DB
- [ ] add migration script to initialize database(drop table and create new)
- [ ] add item to configure db path

# Tests
- [x] Add end to end tests for all agents
  - [x] training_agent
  - [x] meal_agent
  - [x] supervisor_agent
  - [x] configure the project to exclude e2e tests by default

# Misc
- [x] add hooks to pylint the code
- [x] Create Dockerfile and use containers to manage the services
- [x] on_event fastapi deprecated
- [ ] Try out an agent evaluation framework
