"""Batch jobs ported from the VN stack.

Global runs on Koyeb without cron, so every job here is triggered over HTTP from an
admin-protected endpoint (see routes/admin/*_admin.py); each module can still be run
standalone with `python -m app.jobs.<name>`.
"""
