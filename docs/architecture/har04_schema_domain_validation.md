# HAR-04 — strict schema and approved domain-constraint validation

## Boundary

`StrictSchemaDomainValidator.validate()` accepts a parsed `ToolCallRequest`, a
resolved immutable `RegistryTool`, and the caller-held native `params_model`.
It verifies that the model identity and schema equal the registry snapshot, then
calls `params_model.model_validate(arguments, strict=True, extra="forbid")` on
a fresh JSON-decoded copy of request arguments.

The Pydantic output is discarded. HAR-04 does not mutate the request, native
params model, `model_config`, schema, OpenAI schema, global Pydantic settings,
or database. It does not create a ToolMessage or dispatch a tool.

## Error mapping

| Pydantic error type | HAR-04 code |
| --- | --- |
| `missing` | `SCHEMA_MISSING_REQUIRED_ARG` |
| `extra_forbidden` | `SCHEMA_EXTRA_ARGUMENT` |
| strict scalar/container/null type failure | `SCHEMA_WRONG_ARG_TYPE` |
| other Pydantic error | `SCHEMA_VALIDATION_ERROR` |

Each violation stores its field path, message, and canonical sanitized metadata
containing only `pydantic_error_type`; it stores no raw input, URL, context, or
exception object.

## Order and approved constraint

Schema always executes before domain validation. Schema failure returns before
the domain method is entered. The only Retail domain rule is:

```text
modify_pending_order_items: item_ids[i] != new_item_ids[i]
```

Native Retail uses plural list parameters, not scalar `old_item_id` and
`new_item_id`. HAR-04 checks only aligned pairs with `zip` and rejects the
first equal pair as `DOMAIN_CONSTRAINT_VIOLATION`. It intentionally does not
check list length, product compatibility, order state, payment method, or any
other business condition.

All HAR-04 results have `dispatch_attempted=false` and `dispatch_count=0`.

## Evidence and unresolved scope

Installed Pydantic exposes direct `strict` and `extra` arguments on
`BaseModel.model_validate`, so no compatibility adapter is necessary. Retail
native parameter schemas have strings and lists of strings, but no integer or
nested object. The strict `"2"` versus integer `2` test is therefore a minimal
Pydantic dependency fixture, not Retail-native coverage. Nested type behavior
remains untested until a native nested parameter schema enters scope.
