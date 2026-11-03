import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import common as C
import sticky
from local_tasks.core import TaskService, today
from local_tasks.desktop import install

APP = QApplication.instance() or QApplication([])
Manager, Widget = install()


class TaskTabTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(win=dict(w=440, h=440, task_tab='today', ontop=False), tasks=[
            dict(id=f'{key}-{n}', text=f'演示任务 {n}', plan_date=today() if key=='today' else None,
                 done=False, deleted=False, order=n, created=n, updated=n, version=1)
            for key in ('today', 'backlog') for n in range(6)])
        self.writes = []
        for target, kwargs in ((('common.load_tasks'), dict(side_effect=lambda: copy.deepcopy(self.data))),
                               ('common.save_tasks', dict(side_effect=lambda data: self.writes.append(copy.deepcopy(data)))),
                               ('common.set_topmost', {}), ('common.force_foreground', {})):
            mock = patch(target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        self.manager = Manager()
        self.widget = Widget(self.manager)
        self.manager.widget = self.widget
        self.widget.ensurePolished()
        APP.processEvents()

    def tearDown(self):
        self.widget._save_t.stop()
        self.manager._date_timer.stop()
        self.manager._reminder_timer.stop()
        self.widget.hide()
        self.widget.deleteLater()
        APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def ids(self):
        return [row.task['id'] for row in self.widget.rows()]

    def test_warm_switch_reuses_both_views_and_rows(self):
        today_rows = self.widget.rows()
        self.widget.select_tab('backlog')
        backlog_rows = self.widget.rows()
        for _ in range(4):
            self.widget.select_tab('today')
            self.assertEqual(self.widget.rows(), today_rows)
            self.widget.select_tab('backlog')
            self.assertEqual(self.widget.rows(), backlog_rows)
        self.assertEqual(self.widget._page_stack.count(), 2)

    def test_switch_persists_after_click_and_coalesces(self):
        self.writes.clear()
        for key in ('backlog', 'today', 'backlog'):
            self.widget.select_tab(key)
        self.assertEqual(self.writes, [])
        self.assertTrue(self.widget._save_t.isActive())
        QTest.qWait(650)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0]['win']['task_tab'], 'backlog')

    def test_same_tab_click_does_not_rebuild_or_save(self):
        self.writes.clear()
        with patch.object(self.widget, 'rebuild') as rebuild:
            self.widget.tab_buttons['today'].click()
        rebuild.assert_not_called()
        self.assertTrue(self.widget.tab_buttons['today'].isChecked())
        self.assertFalse(self.widget.tab_buttons['backlog'].isChecked())
        self.assertEqual(self.writes, [])

    def test_unknown_tab_is_ignored(self):
        self.widget.select_tab('unknown')
        self.assertEqual(self.manager.win['task_tab'], 'today')
        self.assertTrue(all(tid.startswith('today-') for tid in self.ids()))

    def test_mcp_updates_inactive_page_before_next_display(self):
        self.widget.select_tab('backlog')
        service = TaskService(self.manager)
        service.execute('jianjie_update_tasks', dict(request_id='tab-edit', updates=[
            dict(id='today-0', version=1, patch=dict(text='Agent 更新后的演示任务'))]))
        self.widget.select_tab('today')
        row = next(row for row in self.widget.rows() if row.task['id']=='today-0')
        self.assertEqual(row.label.text(), 'Agent 更新后的演示任务')
        self.assertEqual(row.task['version'], 2)

    def test_move_between_lists_never_duplicates_cached_row(self):
        self.widget.select_tab('backlog')
        self.widget.select_tab('today')
        self.manager.move_task('today-0', 'backlog')
        self.assertNotIn('today-0', self.ids())
        self.widget.select_tab('backlog')
        self.assertEqual(self.ids().count('today-0'), 1)
        self.widget.select_tab('today')
        self.assertNotIn('today-0', self.ids())

    def test_completion_in_flight_is_committed_before_leaving(self):
        row = self.widget.rows()[0]
        row._start_done_transition(True)
        self.widget.select_tab('backlog')
        self.assertTrue(self.manager.tasks[row.task['id']]['done'])
        self.widget.select_tab('today')
        self.assertNotIn(row.task['id'], self.ids())
        self.assertIsNotNone(self.widget.done_hdr())

    def test_inactive_page_cache_has_row_limit(self):
        extra = [dict(self.data['tasks'][0], id=f'large-{n}', order=n+20) for n in range(140)]
        self.manager.tasks.update((task['id'], task) for task in extra)
        self.widget.rebuild()
        self.widget.select_tab('backlog')
        self.assertNotIn('today', self.widget._tab_pages)
        self.assertEqual(self.widget._page_stack.count(), 1)
        self.widget.select_tab('today')
        self.assertEqual(len(self.widget.rows()), 146)

    def test_hiding_window_releases_inactive_page(self):
        self.widget.show()
        APP.processEvents()
        self.widget.select_tab('backlog')
        self.widget.hide()
        APP.processEvents()
        self.assertEqual(set(self.widget._tab_pages), {'backlog'})
        self.assertEqual(self.widget._page_stack.count(), 1)

    def test_switch_keeps_independent_scroll_position(self):
        for key in ('today', 'backlog'):
            for n in range(6, 30):
                task = dict(self.data['tasks'][0], id=f'{key}-{n}', order=n,
                            plan_date=today() if key=='today' else None)
                self.manager.tasks[task['id']] = task
        self.widget.rebuild()
        self.widget.show()
        APP.processEvents()
        self.widget.scroll.verticalScrollBar().setValue(120)
        position = self.widget.scroll.verticalScrollBar().value()
        self.assertGreater(position, 0)
        self.widget.select_tab('backlog')
        APP.processEvents()
        self.widget.scroll.verticalScrollBar().setValue(220)
        other = self.widget.scroll.verticalScrollBar().value()
        self.widget.select_tab('today')
        APP.processEvents()
        self.assertEqual(self.widget.scroll.verticalScrollBar().value(), position)
        self.widget.select_tab('backlog')
        APP.processEvents()
        self.assertEqual(self.widget.scroll.verticalScrollBar().value(), other)

    def test_single_click_switch_commits_current_editor(self):
        self.widget.show()
        APP.processEvents()
        row = self.widget.rows()[0]
        tid = row.task['id']
        self.widget.begin_edit(row)
        self.widget._editing[1].setPlainText('编辑后切换列表')
        self.widget.select_tab('backlog')
        self.assertIsNone(self.widget._editing)
        self.assertEqual(self.manager.tasks[tid]['text'], '编辑后切换列表')
        self.assertEqual(self.manager.win['task_tab'], 'backlog')
        APP.processEvents()
        self.widget.select_tab('today')
        row = next(row for row in self.widget.rows() if row.task['id']==tid)
        self.assertEqual(row.label.text(), '编辑后切换列表')

    def test_cached_scope_updates_across_date_rollover(self):
        task = dict(self.data['tasks'][0], id='future', plan_date='2099-01-01')
        self.manager.tasks['future'] = task
        self.widget.rebuild()
        self.widget.select_tab('backlog')
        self.assertIn('future', self.ids())
        with patch('local_tasks.core.today', return_value='2099-01-01'), \
                patch('local_tasks.desktop.today', return_value='2099-01-01'):
            self.widget.select_tab('today')
            self.assertIn('future', self.ids())
            self.widget.select_tab('backlog')
            self.assertNotIn('future', self.ids())

    def test_deleted_inactive_task_is_not_shown_from_cache(self):
        self.widget.select_tab('backlog')
        self.manager.delete('today-0')
        self.widget.select_tab('today')
        self.assertNotIn('today-0', self.ids())

    def test_done_collapse_setting_is_shared_across_cached_views(self):
        self.manager.tasks['today-0']['done'] = True
        self.manager.tasks['backlog-0']['done'] = True
        self.widget.rebuild()
        self.widget.toggle_done_collapsed()
        self.assertIn('today-0', self.ids())
        self.widget.select_tab('backlog')
        self.assertIn('backlog-0', self.ids())
        self.widget.toggle_done_collapsed()
        self.widget.select_tab('today')
        self.assertNotIn('today-0', self.ids())


if __name__ == '__main__':
    unittest.main()
