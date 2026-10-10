/-
Declarations of the pinned Mathlib, one JSON object per line, for the
semantic index (aiprover_orchestration/search/build_index.py).

  MATHLIB_INDEX_KNOWN=<names file> MATHLIB_INDEX_OUT=<jsonl> \
    lake env lean extract_declarations.lean

Each line holds the name, module, kind and docstring. The type is printed
only for names absent from the known-names file (the informalized corpus),
whose own entries carry types.
-/
import Mathlib

open Lean Elab Command Meta

def kindOf : ConstantInfo → String
  | .thmInfo _ => "theorem"
  | .defnInfo _ => "definition"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .axiomInfo _ => "axiom"
  | .opaqueInfo _ => "opaque"
  | .recInfo _ => "recursor"
  | .quotInfo _ => "quotient"

#eval show CommandElabM Unit from do
  let known ← IO.FS.lines (← IO.getEnv "MATHLIB_INDEX_KNOWN").get!
  let known : Std.HashSet String := known.foldl (·.insert ·) {}
  let out ← IO.FS.Handle.mk (← IO.getEnv "MATHLIB_INDEX_OUT").get! .write
  let env ← getEnv
  for (name, info) in env.constants.map₁.toList do
    if name.isInternalDetail || isPrivateName name then continue
    let some index := env.getModuleIdxFor? name | continue
    let module := env.header.moduleNames[index.toNat]!
    unless (`Mathlib).isPrefixOf module do continue
    let kind := kindOf info
    if kind == "recursor" || kind == "quotient" then continue
    let doc ← findDocString? env name
    let type ← if known.contains name.toString then pure "" else
      liftTermElabM do return toString (← ppExpr info.type)
    out.putStrLn <| Json.compress <| Json.mkObj [
      ("name", toJson name.toString), ("module", toJson module.toString),
      ("kind", toJson kind), ("type", toJson type),
      ("doc", toJson (doc.getD ""))]
