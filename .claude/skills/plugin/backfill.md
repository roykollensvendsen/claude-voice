# Bringing an existing plugin up to the template

You need: [SKILL.md](SKILL.md) and [rules.md](rules.md).

## 1. List what fails

Diff the plugin's repository against [templates/plugin/](../../../templates/plugin/README.md) at
main. Read each difference against rules.md, and list the rule numbers that fail. A difference
no rule covers is a real difference of the plugin, and stays.

Read a rule's scope before failing it. A plugin may run from a branch other than main, and a
suite that imports no Python package skips R23.

## 2. One pull request per rule

Make one pull request for each failing rule, each red first (R12). Its title or body names the
rule: "R22: the ratchet sends its stop to the child alone".

Copy the file the rule names from `templates/plugin/` at main, never from another plugin, and
name the template's commit in the commit body.

## 3. Licence before copying

The template is GPL-3.0. A plugin under another licence, MIT for one, is written from the rule's
description, never by copying, until the owner decides the template's licence.

## 4. The links in the plugin

A line in the plugin's own CONTRIBUTING or AGENTS that points at the old `write` skill changes to
this skill in the plugin's next backfill pull request. There is no separate sweep.
