# Agents
## supervisor_agent
- [x] graph to conditionally call the subagents
- [x] add the ability for casual chat
- [x] bug: fix the slowness of supervisor_agent
  - [x] system prompt, conditional edges
  - [x] support using proxy
  - [x] minimize llm calls to decrease the chances of using proxy

## training_agent
- [ ] add tool to expand acronym
- [ ] add tool to retrospect training history using time range provided by user

## meal_agent
- [ ] ambitious: add tool to analyze nutrition
- [ ] add tool to retrospect meal history using time range provided by user.


# UI
- [x] basic chat interface, using rich

# DB
- [ ] add migration script to initialize database(drop table and create new)
- [ ] add item to configure db path


# Tests
- [x] Add end to end tests for all agents
  - [x] training_agent
  - [x] meal_agent
  - [x] supervisor_agent
  - [x] configure the project to exclude e2e tests by default

# Misc
- [ ] add hooks to pylint the code
