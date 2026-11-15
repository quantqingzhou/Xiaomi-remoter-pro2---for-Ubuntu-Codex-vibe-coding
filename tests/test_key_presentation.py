# SPDX-License-Identifier: GPL-3.0-only
"""可视化按键说明与运行状态不能互相冒充，也不能覆盖用户自定义。"""
import support
import copy
import unittest
from keymap_config import defaults, BUTTONS
from key_presentation import button_view, runtime_mode_status


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.data = defaults()
        self.data['extra_buttons'] = {'home': 102, 'tv': 41}
        self.data['navigation'] = {'enabled': True}
        self.data['profiles']['codex']['home'] = 'Super'
        self.data['profiles']['wechat'].update(home='Super', tv='Alt+A')

    def view(self, profile, mode, button):
        return button_view(self.data, profile, mode, button)

    def test_preview_does_not_modify_configuration(self):
        before = copy.deepcopy(self.data)
        for profile in ('codex','wechat','claude','other'):
            for mode in ('normal','mouse'):
                for name in BUTTONS:
                    view = self.view(profile,mode,name)
                    self.assertTrue(view.short and view.title and view.detail)
        self.assertEqual(self.data,before)

    def test_claude_dedicated_keys_do_not_inherit_other_overrides(self):
        self.data['profiles']['other'].update(back='Ctrl+Z', confirm='disabled')
        for button, text in (('voice','Ctrl + D'),('confirm','Enter'),('back','Backspace')):
            view = self.view('claude','normal',button)
            self.assertIn(text, view.title)
            self.assertFalse(view.editable)
            self.assertTrue(view.fixed)
        self.assertIn('Backspace',self.view('claude','mouse','back').title)
        self.assertIn('右键',self.view('claude','mouse','confirm').title)
        self.assertIn('退出',self.view('claude','mouse','voice').title)

    def test_claude_shared_keys_read_current_other_mapping_without_new_profile(self):
        self.data['profiles']['other'].update(home='Super',tv='Alt+A')
        before = copy.deepcopy(self.data)
        for mode in ('normal','mouse'):
            view = self.view('claude',mode,'tv')
            self.assertIn('Alt',view.title)
            self.assertIn('其他软件',view.detail)
            self.assertFalse(view.editable or view.fixed)
            self.assertIn('Super',self.view('claude',mode,'home').title)
        self.assertEqual(before,self.data)
        self.assertNotIn('claude',self.data['profiles'])

    def test_overview_help_matches_normal_confirm_and_short_home(self):
        for profile in ('codex','wechat','claude','other'):
            for button in ('home','confirm'):
                self.assertIn('活动概览',self.view(profile,'normal',button).detail)
        self.assertNotIn('活动概览',self.view('claude','mouse','confirm').detail)

    def test_mouse_direction_masks_custom_normal_action_without_erasing_it(self):
        self.data['profiles']['codex']['up']='Ctrl+Z'
        self.assertIn('撤销',self.view('codex','normal','up').title)
        mouse=self.view('codex','mouse','up')
        self.assertIn('移动鼠标',mouse.title)
        self.assertFalse(mouse.editable)
        self.assertTrue(mouse.fixed)
        self.assertEqual(self.data['profiles']['codex']['up'],'Ctrl+Z')

    def test_wechat_mouse_back_is_editable_inherited_backspace_not_escape(self):
        view=self.view('wechat','mouse','back')
        self.assertIn('Backspace',view.title)
        self.assertIn('连续删除',view.detail)
        self.assertTrue(view.editable)
        self.assertIn('共用',view.edit_label)
        self.data['profiles']['wechat']['back']='Delete'
        self.assertIn('Delete',self.view('wechat','mouse','back').title)

    def test_codex_mouse_back_is_fixed_escape_despite_custom_normal_back(self):
        self.data['profiles']['codex']['back']='Ctrl+Z'
        view=self.view('codex','mouse','back')
        self.assertIn('Esc',view.title)
        self.assertFalse(view.editable)
        self.assertIn('撤销',self.view('codex','normal','back').title)

    def test_confirm_and_volume_describe_mouse_not_send_or_volume(self):
        view=self.view('codex','mouse','confirm')
        self.assertIn('右键',view.title);self.assertIn('0.6',view.detail)
        self.assertFalse(view.editable)
        for name in ('volume_up','volume_down'):
            view=self.view('wechat','mouse',name)
            self.assertIn('滚动',view.title);self.assertFalse(view.editable)

    def test_home_distinguishes_short_config_and_long_toggle(self):
        normal=self.view('codex','normal','home')
        mouse=self.view('codex','mouse','home')
        self.assertIn('Super',normal.title);self.assertIn('进入',normal.title)
        self.assertIn('Super',mouse.title);self.assertIn('退出',mouse.title)
        self.assertTrue(normal.editable and mouse.editable)
        self.data['navigation']['enabled']=False
        self.assertNotIn('长按',self.view('codex','normal','home').title)

    def test_unlearned_home_cannot_be_presented_as_working(self):
        del self.data['extra_buttons']['home']
        self.assertEqual(self.view('codex','mouse','home').short,'待识别')
        self.assertIn('先识别',self.view('codex','mouse','home').detail)

    def test_wechat_tv_preserves_shortcut_and_is_shared(self):
        normal=self.view('wechat','normal','tv')
        mouse=self.view('wechat','mouse','tv')
        self.assertEqual(normal.title,mouse.title)
        self.assertEqual(mouse.title, 'Alt + A')
        self.assertIn('Alt',mouse.title);self.assertTrue(mouse.editable)
        self.assertIn('共用',mouse.edit_label)

    def test_voice_mouse_mode_does_not_promise_dictation(self):
        view=self.view('wechat','mouse','voice')
        self.assertIn('退出',view.title)
        self.assertIn('只显示提示',view.detail)
        self.assertFalse(view.editable)

    def test_other_software_has_global_mouse_overrides_and_claude_back_note(self):
        for button in ('up','down','left','right','confirm','back','voice','volume_up','volume_down'):
            self.assertTrue(self.view('other','mouse',button).fixed)
            self.assertNotEqual(self.view('other','normal',button),self.view('other','mouse',button))
        self.assertIn('Claude',self.view('other','mouse','back').detail)
        self.assertIn('长按',self.view('other','normal','home').title)
        self.assertIn('退出',self.view('other','mouse','home').title)

    def test_disabled_key_and_incomplete_custom_text_are_renderable(self):
        self.data['profiles']['wechat']['back']='disabled'
        view=self.view('wechat','mouse','back')
        self.assertIn('不执行',view.title)
        self.data['profiles']['wechat']['back']='Ctrl+'
        self.assertTrue(self.view('wechat','mouse','back').editable)


class RuntimeStatusTests(unittest.TestCase):
    def test_live_mouse_status_names_actual_application(self):
        self.assertEqual(runtime_mode_status({'time':100},{'time':100,'active':True,'context':'wechat'},101),
                         ('mouse','当前：全局鼠标模式 · 微信'))
        for context, label in (('claude','Claude'),('other','其他软件')):
            self.assertEqual(runtime_mode_status({'time':100},{'time':100,'active':True,'context':context},101),
                             ('mouse','当前：全局鼠标模式 · '+label))

    def test_preview_or_draft_configuration_is_not_an_input(self):
        self.assertEqual(runtime_mode_status({'time':100},{},101),('normal','当前：普通模式'))
        self.assertEqual(runtime_mode_status({'time':100},{'time':20,'active':False},101)[0],'normal')

    def test_stale_mouse_or_dead_bridge_never_shows_active_mouse(self):
        self.assertEqual(runtime_mode_status({'time':100},{'time':90,'active':True,'context':'codex'},101)[0],'unknown')
        self.assertEqual(runtime_mode_status({'time':90},{'time':100,'active':True,'context':'codex'},101)[0],'unknown')

    def test_paused_requires_missing_heartbeat(self):
        self.assertEqual(runtime_mode_status({}, {},100,paused=True)[0],'paused')
        self.assertEqual(runtime_mode_status({'time':100},{},101,paused=True)[0],'normal')

    def test_bad_timestamps_and_contexts_are_unknown(self):
        for nav in ({'time':True,'active':True,'context':'codex'},
                    {'time':105,'active':True,'context':'codex'},
                    {'time':100,'active':True,'context':'unknown'},
                    {'time':100,'active':'true','context':'codex'}):
            self.assertEqual(runtime_mode_status({'time':100},nav,101)[0],'unknown')


if __name__=='__main__': unittest.main()
