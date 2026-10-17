"""Lead diagnostic: dump generated source for CAP/M0 to pinpoint NameError.

Temporary probe -- deleted after use.  Kept out of the way of teammate scopes.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dcg.codegen as cg

CAP = {"name": "cap", "input": {"source": "rows"},
       "output": {"shape": "list", "single": False, "filter": True},
       "out": {"type": "object", "fields": {
           "hourA": {"type": "int64", "value": "$.5.a.1"},
           "hourB": {"type": "int64", "value": "$.5.a.2"},
           "hourD": {"type": "str", "expr": "`str($.5.a.1) + str(hourB)`"},
           "hourE": {"type": "int64", "expr": "hourA + hourB"}}}}

M0 = {"name": "m0", "input": {"source": "index"},
      "output": {"shape": "list", "single": False, "filter": True},
      "out": {"type": "object", "fields": {
          "hour":  {"type": "list",   "value": "$.5.a"},
          "hourA": {"type": "int64",  "value": "$.5.a.1"},
          "hourB": {"type": "int64",  "value": "$.5.a.2"},
          "hourC": {"type": "int64",  "value": "$.2"},
          "hourD": {"type": "str", "expr": "`str($.5.a.1) + str(hourB)`"},
          "hourE": {"type": "int64",  "expr": "hourA + hourB"},
          "hourF": {"type": "int64",  "expr": "$.5.a.2 + $.5.a.1"},
          "hourG": {"type": "int64",  "expr": "hour[2] + 123"}}}}

for schema in (CAP, M0):
    src = cg.generate(schema)
    print("=" * 70)
    print(f"### {schema['name']}  ({len(src)} chars)")
    print("=" * 70)
    print(src)
    print()
