# -*- coding: utf-8 -*-
"""
蛋国政务管理系统（政务服务一体化平台版）
=============================================
对齐真实"一网通办 / 网上办事大厅"形态，构建民众办事、官员办公、系统管理三大端。

【民众端（办事人）】
  · 办事服务大厅：分类事项库 + 在线申办（办件编号 + 进度全流程）
  · 我的办件：进度跟踪、撤回、服务评价（好差评）
  · 办事预约：在线预约办理窗口/时段
  · 办件进度查询：凭办件编号公开查询
  · 政务公开：通知公告 + 政策文件库
  · 互动交流：政务咨询 / 投诉举报 / 意见建议
  · 我的申请（文件审批）、我的户籍、我的消息

【官员端（工作人员）】
  · 工作台、待办审批（按部门流转）、办件办理、公文管理（收发文）
  · 群众建议 / 咨询回复、工作报告、审批历史、政务公开、我的信息、我的消息

【管理员端（系统管理）】
  · 官员任免管理、办事事项管理、办件管理、预约管理、公文管理
  · 政策文件管理、公告管理、举报处理、咨询管理、起诉官员（对接法院）
  · 消息广播、系统日志、数据统计、修改密码

对接：户籍系统数据库(household_registration.db)、法院系统数据库(court_system.db)
内部数据：gov_system.db
"""
import sqlite3
import random
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
import os
import sys
import io
import base64
import atexit
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

# ===== 联网版：数据库路由到 Turso 云端（配置见同目录 turso_config.json）=====
try:
    import remote_db
    remote_db.install()
except Exception as _rd_err:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'remote_db_error.log'), 'a', encoding='utf-8') as _lf:
            _lf.write(str(_rd_err) + '\n')
    except Exception:
        pass

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

# ========== 路径配置 ==========
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HOUSEHOLD_DB = os.path.join(BASE_DIR, 'household_registration.db')
COURT_DB = os.path.join(BASE_DIR, 'court_system.db')
GOV_DB = os.path.join(BASE_DIR, 'gov_system.db')
BANK_DB = os.path.join(BASE_DIR, 'bank_system.db')
# 蛋国选举人团：6 州共 280 张选举人票，150 票获胜
ELECTORAL_STATES = ['海文州', '维拉州', '奥林州', '凯林州', '米伦州', '索伦州']
ELECTORAL_VOTES = {'海文州': 46, '维拉州': 46, '奥林州': 47,
                   '凯林州': 47, '米伦州': 47, '索伦州': 47}
ELECTORAL_MAJORITY = 150
# 监狱管理系统（商业版）：其数据与户籍系统共用 household_registration.db，囚犯等业务表以 pz_ 前缀
PRISON_DB = HOUSEHOLD_DB

# ========== 官员肖像 ==========
# 肖像图片直接存数据库（officials.photo 列），值为 "b64:<base64(JPEG)>"；
# 兼容旧数据：列里若还是 official_portraits/xxx.png 这类相对路径，仍按本地文件读取。
PORTRAIT_B64_PREFIX = 'b64:'
PORTRAIT_DIR = os.path.join(BASE_DIR, 'official_portraits')
try:
    from PIL import Image, ImageTk, ImageDraw, ImageOps
    HAS_PIL = True
except Exception:
    HAS_PIL = False


def portrait_abs(path):
    """肖像相对路径 → 绝对路径（空值 / 已内嵌在数据库里时返回空串）"""
    if not path:
        return ''
    if str(path).startswith(PORTRAIT_B64_PREFIX):
        return ''
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def portrait_is_embedded(path):
    """photo 列是否存的数据库内嵌图片（b64:...）"""
    return bool(path) and str(path).startswith(PORTRAIT_B64_PREFIX)


def portrait_bytes(path):
    """取出肖像的二进制内容：库内嵌优先，兼容旧的本地文件路径"""
    if not path:
        return None
    if portrait_is_embedded(path):
        try:
            return base64.b64decode(str(path)[len(PORTRAIT_B64_PREFIX):])
        except Exception:
            return None
    abs_path = portrait_abs(path)
    if abs_path and os.path.exists(abs_path):
        try:
            with open(abs_path, 'rb') as f:
                return f.read()
        except Exception:
            return None
    return None


def portrait_image(path):
    """肖像 → PIL.Image（库里内嵌的或旧的本地文件都支持）；取不到返回 None"""
    if not HAS_PIL:
        return None
    data = portrait_bytes(path)
    if not data:
        return None
    try:
        return Image.open(io.BytesIO(data)).convert('RGB')
    except Exception:
        return None


def save_official_portrait(src_path, official_id, crop_box=None):
    """把用户选择的图片处理成 400x400 正方形肖像，直接编码成 JPEG 存进数据库
    （officials.photo 列，形式 "b64:<base64>"），返回该字符串；失败返回 (None, msg)。
    crop_box 为原图坐标 (left, top, right, bottom)（正方形），为空则居中裁剪。"""
    if not HAS_PIL:
        return None, '缺少 Pillow 图片库，无法处理肖像'
    try:
        img = Image.open(src_path)
        img = ImageOps.exif_transpose(img).convert('RGB')
        w, h = img.size
        if crop_box:
            box = [int(crop_box[0]), int(crop_box[1]), int(crop_box[2]), int(crop_box[3])]
            box[0] = max(0, min(box[0], w - 1))
            box[1] = max(0, min(box[1], h - 1))
            box[2] = max(box[0] + 10, min(box[2], w))
            box[3] = max(box[1] + 10, min(box[3], h))
            # 强制正方形（以较小边为准）
            side = min(box[2] - box[0], box[3] - box[1])
            img = img.crop((box[0], box[1], box[0] + side, box[1] + side))
        else:
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            img = img.crop((left, top, left + side, top + side))
        if img.size[0] != 400:
            img = img.resize((400, 400), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=86, optimize=True)
        # 限制体积：超过 120KB 再降一档质量，避免把库撑大
        if buf.tell() > 120 * 1024:
            buf = io.BytesIO()
            img.save(buf, 'JPEG', quality=72, optimize=True)
        return PORTRAIT_B64_PREFIX + base64.b64encode(buf.getvalue()).decode('ascii'), ''
    except Exception as e:
        return None, f'图片处理失败：{e}'


def make_portrait_photo(path, size=120, circular=True, bg='white'):
    """加载官员肖像并返回 Tk PhotoImage（圆形裁剪）；无图/加载失败时返回打了
    👤 的灰色占位圆。肖像可来自数据库内嵌内容，也兼容旧的本地文件。返回 (photo, has_real)。"""
    if not HAS_PIL:
        return None, False
    src_img = portrait_image(path)
    has_real = src_img is not None
    try:
        if has_real:
            img = src_img.convert('RGBA')
            w, h = img.size
            side = min(w, h)
            img = img.crop(((w - side) // 2, (h - side) // 2,
                            (w - side) // 2 + side, (h - side) // 2 + side))
            img = img.resize((size, size), Image.LANCZOS)
        else:
            img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            if circular:
                d.ellipse((0, 0, size - 1, size - 1), fill=(0xE8, 0xEA, 0xEE, 255))
            else:
                d.rectangle((0, 0, size - 1, size - 1), fill=(0xE8, 0xEA, 0xEE, 255))
            # 简单人形剪影
            cx = size / 2
            r = size * 0.16
            d.ellipse((cx - r, size * 0.18, cx + r, size * 0.18 + 2 * r), fill=(0xB8, 0xBE, 0xC6, 255))
            d.chord((cx - size * 0.28, size * 0.52, cx + size * 0.28, size * 1.06),
                    180, 360, fill=(0xB8, 0xBE, 0xC6, 255))
        if circular:
            mask = Image.new('L', (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
            out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            out.paste(img, (0, 0), mask)
        else:
            out = img
        return ImageTk.PhotoImage(out), has_real
    except Exception:
        return None, False


class PortraitCropDialog:
    """肖像裁剪对话框：拖动方框选区域、拖动四角缩放（保持正方形），
    确认后回调原图坐标 (left, top, right, bottom)。"""

    MAX_W, MAX_H, MIN_SIDE = 520, 400, 60

    def __init__(self, master, src_path, on_confirm, title='裁剪肖像'):
        self.on_confirm = on_confirm
        self.win = tk.Toplevel(master)
        self.win.title(title)
        self.win.configure(bg='#1c1c1e')
        self.win.transient(master)
        self.win.grab_set()
        self.win.resizable(False, False)

        img = Image.open(src_path)
        img = ImageOps.exif_transpose(img).convert('RGB')
        self.orig_w, self.orig_h = img.size
        scale = min(self.MAX_W / self.orig_w, self.MAX_H / self.orig_h, 1.0)
        self.disp_w = max(1, int(self.orig_w * scale))
        self.disp_h = max(1, int(self.orig_h * scale))
        self.fx = self.orig_w / self.disp_w
        self.fy = self.orig_h / self.disp_h
        self._disp = ImageTk.PhotoImage(img.resize((self.disp_w, self.disp_h), Image.LANCZOS))

        tk.Label(self.win, text='拖动方框选择头像区域 · 拖动四角可缩放 · 框内拖动可移动',
                 font=('Arial', 10), bg='#1c1c1e', fg='#c7c7cc').pack(pady=(12, 2))
        self.pad = 18
        cw, ch = self.disp_w + self.pad * 2, self.disp_h + self.pad * 2
        self.cv = tk.Canvas(self.win, width=cw, height=ch, bg='#1c1c1e',
                            highlightthickness=0, cursor='fleur')
        self.cv.pack(padx=8)
        self.ox = (cw - self.disp_w) // 2
        self.oy = (ch - self.disp_h) // 2
        self.cv.create_image(self.ox, self.oy, anchor='nw', image=self._disp)

        init_side = min(self.disp_w, self.disp_h) * 0.88
        self.box = [self.ox + (self.disp_w - init_side) / 2,
                    self.oy + (self.disp_h - init_side) / 2, init_side]
        self.shade = None
        self.rect = self.cv.create_rectangle(0, 0, 0, 0, outline='#0a84ff', width=2)
        self.handles = []
        self._mode = None
        self._drag_start = (0, 0)
        self._box0 = list(self.box)
        self._draw()

        self.cv.bind('<Button-1>', self._on_press)
        self.cv.bind('<B1-Motion>', self._on_drag)
        self.cv.bind('<ButtonRelease-1>', self._on_release)

        btns = tk.Frame(self.win, bg='#1c1c1e')
        btns.pack(pady=12)
        make_flat_button(btns, '↺ 重置', self._reset, '#3a3a3c', '#48484a',
                         width=8, height=1).pack(side='left', padx=4)
        make_flat_button(btns, '取消', self.win.destroy, '#3a3a3c', '#48484a',
                         width=8, height=1).pack(side='left', padx=4)
        make_flat_button(btns, '✅ 使用此裁剪', self._confirm, COLOR_PRIMARY,
                         COLOR_PRIMARY_HOVER, width=14, height=1).pack(side='left', padx=4)

    # ---------- 绘制 ----------
    def _draw(self):
        x, y, s = self.box
        for h in self.handles:
            self.cv.delete(h)
        self.handles = []
        for g in getattr(self, 'guides', []):
            self.cv.delete(g)
        self.guides = []
        self.cv.coords(self.rect, x, y, x + s, y + s)
        # 三分辅助线
        for i in (1, 2):
            self.guides.append(self.cv.create_line(x + s * i / 3, y, x + s * i / 3, y + s,
                                                   fill='#0a84ff', dash=(3, 4)))
            self.guides.append(self.cv.create_line(x, y + s * i / 3, x + s, y + s * i / 3,
                                                   fill='#0a84ff', dash=(3, 4)))
        for cx, cy in ((x, y), (x + s, y), (x, y + s), (x + s, y + s)):
            self.handles.append(self.cv.create_rectangle(
                cx - 5, cy - 5, cx + 5, cy + 5, outline='#0a84ff', fill='#ffffff', width=1))

    # ---------- 交互 ----------
    def _on_press(self, e):
        px, py = e.x, e.y
        x, y, s = self.box
        tol = 13
        self._mode = None
        for k, (cx, cy) in (('nw', (x, y)), ('ne', (x + s, y)),
                            ('sw', (x, y + s)), ('se', (x + s, y + s))):
            if abs(px - cx) <= tol and abs(py - cy) <= tol:
                self._mode = k
                break
        if self._mode is None and x <= px <= x + s and y <= py <= y + s:
            self._mode = 'move'
        if self._mode is None:
            return 'break'
        self.cv.configure(cursor=('fleur' if self._mode == 'move' else 'crosshair'))
        self._drag_start = (px, py)
        self._box0 = list(self.box)

    def _on_drag(self, e):
        if not self._mode:
            return
        px, py = e.x, e.y
        dx, dy = px - self._drag_start[0], py - self._drag_start[1]
        x0, y0, s0 = self._box0
        minx, miny = self.ox, self.oy
        maxx, maxy = self.ox + self.disp_w, self.oy + self.disp_h
        if self._mode == 'move':
            nx = min(max(x0 + dx, minx), maxx - s0)
            ny = min(max(y0 + dy, miny), maxy - s0)
            self.box = [nx, ny, s0]
        elif self._mode == 'se':
            ax, ay = x0, y0
            side = min(max(self.MIN_SIDE, max(px - ax, py - ay)), maxx - ax, maxy - ay)
            self.box = [ax, ay, side]
        elif self._mode == 'nw':
            ax, ay = x0 + s0, y0 + s0
            side = min(max(self.MIN_SIDE, max(ax - px, ay - py)), ax - minx, ay - miny)
            self.box = [ax - side, ay - side, side]
        elif self._mode == 'ne':
            ax, ay = x0, y0 + s0
            side = min(max(self.MIN_SIDE, max(px - ax, ay - py)), maxx - ax, ay - miny)
            self.box = [ax, ay - side, side]
        else:  # sw
            ax, ay = x0 + s0, y0
            side = min(max(self.MIN_SIDE, max(ax - px, py - ay)), ax - minx, maxy - ay)
            self.box = [ax - side, ay, side]
        self._draw()

    def _on_release(self, _e=None):
        self._mode = None

    def _reset(self):
        init_side = min(self.disp_w, self.disp_h) * 0.88
        self.box = [self.ox + (self.disp_w - init_side) / 2,
                    self.oy + (self.disp_h - init_side) / 2, init_side]
        self._draw()

    def _confirm(self):
        x, y, s = self.box
        cx = (x - self.ox) * self.fx
        cy = (y - self.oy) * self.fy
        l = max(0, min(int(cx), self.orig_w - 10))
        t = max(0, min(int(cy), self.orig_h - 10))
        side = min(int(s * max(self.fx, self.fy)), self.orig_w - l, self.orig_h - t)
        box = (l, t, l + side, t + side)
        self.win.destroy()
        self.on_confirm(box)

# ========== 常量 ==========
# 旧体系（V1，迁移对照用）
RANKS_OLD = ['见习官员', '九级官员', '八级官员', '七级官员', '六级官员',
             '五级官员', '四级官员', '三级官员', '二级官员', '一级官员', '特级官员']
DEPARTMENTS_OLD = ['国务院', '财政部', '教育部', '公安部', '司法部', '住建部',
                   '交通部', '农业部', '商务部', '卫生部', '环保部', '人社部', '民政部']
POSITIONS_OLD = ['部长', '副部长', '司长', '副司长', '处长', '副处长', '科长', '副科长', '办事员']

# 新体系（V2 美式联邦制）
# 职级：政治任命 ES-1~ES-5（越大越高）> 高级行政官员 SES > 职业文官 GS-15~GS-1
RANKS = ['GS-1级文官', 'GS-2级文官', 'GS-3级文官', 'GS-4级文官', 'GS-5级文官',
         'GS-6级文官', 'GS-7级文官', 'GS-8级文官', 'GS-9级文官', 'GS-10级文官',
         'GS-11级文官', 'GS-12级文官', 'GS-13级文官', 'GS-14级文官', 'GS-15级文官',
         '高级行政官员(SES)', 'ES-5级政治任命', 'ES-4级政治任命', 'ES-3级政治任命',
         'ES-2级政治任命', 'ES-1级政治任命']
# 部门：三权分立（行政=总统府+内阁15部+独立机构；立法=议会两院；司法=最高法院+联邦法院；州=6州）
DEPARTMENTS = [
    # 行政分支顶层
    '总统府',
    # 内阁15部
    '国务院', '财政部', '国防部', '司法部', '内政部', '农业部', '商务部', '劳工部',
    '卫生与公众服务部', '住房与城市发展部', '交通部', '能源部', '教育部',
    '退伍军人事务部', '国土安全部',
    # 独立机构
    '中央情报局', '联邦调查局', '环境保护署', '国家央行',
    # 立法分支
    '议会', '参议院', '众议院',
    # 司法分支
    '最高法院', '联邦上诉法院', '联邦地区法院',
    # 州
    '一州', '二州', '三州', '四州', '五州', '六州',
]
# 职务：全美式官方名
POSITIONS = [
    # 国家元首/政府首脑
    '总统', '副总统',
    # 内阁部长
    '国务卿', '财政部长', '国防部长', '司法部长', '内政部长', '农业部长', '商务部长',
    '劳工部长', '卫生与公众服务部长', '住房与城市发展部长', '交通部长', '能源部长',
    '教育部长', '退伍军人事务部长', '国土安全部长',
    # 独立机构首长
    '中央情报局局长', '联邦调查局局长', '环境保护署署长', '国家央行行长',
    # 立法
    '参议院议长', '众议院议长', '参议员', '众议员',
    # 司法
    '最高法院首席大法官', '最高法院大法官', '联邦上诉法官', '联邦地区法官',
    # 州级
    '州长', '副州长', '州议员',
    # 职业文官（保留部门内设层级，美式官方译名）
    '高级行政官员', '司长', '副司长', '处长', '副处长', '科长', '副科长', '办事员',
]

# 三权分支标记（部门所属分支，用于权力分级）
BRANCH_ADMIN = '行政'
BRANCH_LEGISLATE = '立法'
BRANCH_JUDICIARY = '司法'
BRANCH_STATE = '州'
BRANCH_NONE = '其他'

# ========== 权限项（官员可被授予的操作） ==========
# key 与 rank_levels 默认位 / official_perm_overrides 对应
PERM_KEYS = ['send_clause', 'approve', 'send_doc', 'handle']
PERM_LABELS = {
    'send_clause': '发送条款（起草呈批件/条款送审）',
    'approve': '审批决定（对待办通过/驳回）',
    'send_doc': '公文发文（对外/跨部门发文）',
    'handle': '办件办理与互动（办件、回复咨询举报、工作报告）',
}
PERM_RANK_COL = {'send_clause': 'd_send_clause', 'approve': 'd_approve',
                 'send_doc': 'd_send_doc', 'handle': 'd_handle'}
# 职务表对应列：取值 default(跟随职级) / allow / deny
PERM_POS_COL = {'send_clause': 'p_send_clause', 'approve': 'p_approve',
                'send_doc': 'p_send_doc', 'handle': 'p_handle'}
# 审批范围档位
APPROVE_SCOPES = ['none', 'self', 'subtree', 'custom', 'all']
SCOPE_LABELS = {
    'none': '无审批权', 'self': '仅本部门', 'subtree': '本部门及下级部门',
    'custom': '指定部门', 'all': '全部部门',
}

# 政务服务事项分类
SERVICE_CATEGORIES = ['户籍事务', '婚姻生育', '社会保障', '就业创业',
                      '证件办理', '民政救济', '企业服务', '综合服务']
# 办件状态流转
CASE_STATUS = ['待受理', '受理中', '办理中', '已办结', '已退回']
# 预约时段
APPT_SLOTS = ['09:00-10:00', '10:00-11:00', '11:00-12:00',
              '14:00-15:00', '15:00-16:00', '16:00-17:00']
# 咨询分类
CONSULT_CATEGORIES = ['办事流程', '政策法规', '费用标准', '其他']
# 公文紧急程度
DOC_URGENCY = ['普通', '加急', '特急']
# 公文类型
DOC_TYPES = ['收文', '发文']

# 颜色主题（银行系统风格）
COLOR_TOP_BAR = '#34495e'
COLOR_MENU_BG = '#f0f0f0'
COLOR_MENU_BTN = '#e1e1e1'
COLOR_MENU_HOVER = '#d0d0d0'
COLOR_CONTENT_BG = 'white'
COLOR_TITLE = '#2c3e50'
COLOR_PRIMARY = '#3498db'
COLOR_PRIMARY_HOVER = '#2980b9'
COLOR_SUCCESS = '#27ae60'
COLOR_SUCCESS_HOVER = '#229954'
COLOR_WARNING = '#e67e22'
COLOR_DANGER = '#e74c3c'
COLOR_DANGER_HOVER = '#c0392b'
COLOR_PURPLE = '#9b59b6'
COLOR_GRAY = '#95a5a6'
COLOR_TEXT_GRAY = '#7f8c8d'

# 政务服务事项初始化数据
# (事项编号, 名称, 分类, 承办部门, 说明, 所需材料, 法律依据, 办理时限, 收费标准)
SERVICE_ITEMS_SEED = [
    ('GG-001', '户口迁移办理', '户籍事务', '公安部',
     '因工作、学习、投靠等原因办理户口迁移登记。',
     '身份证、户口簿、迁移事由证明（工作证明/录取通知书/亲属关系证明等）、房屋产权证明',
     '《中华人民共和国户口登记条例》第十条', '10个工作日', '免费'),
    ('GG-002', '新生儿出生落户', '户籍事务', '公安部',
     '为符合条件的新生儿办理出生登记落户。',
     '出生医学证明、父母身份证、父母户口簿、结婚证',
     '《中华人民共和国户口登记条例》第七条', '5个工作日', '免费'),
    ('GG-003', '居民身份证申领', '户籍事务', '公安部',
     '年满16周岁公民首次申领居民身份证。',
     '户口簿、本人近期免冠照片（现场采集）',
     '《中华人民共和国居民身份证法》第八条', '15个工作日', '工本费20元'),
    ('GG-004', '居民身份证补领', '户籍事务', '公安部',
     '身份证丢失、损毁或有效期届满换领。',
     '户口簿、身份证挂失申报回执（丢失时）',
     '《中华人民共和国居民身份证法》第十一条', '15个工作日', '工本费40元'),
    ('GG-005', '户口簿补办', '户籍事务', '公安部',
     '户口簿丢失或损毁后申请补发。',
     '户主身份证、遗失声明',
     '《中华人民共和国户口登记条例》', '即时办理', '工本费6元'),
    ('HY-001', '结婚登记', '婚姻生育', '民政部',
     '男女双方自愿结婚的登记服务。',
     '双方身份证、双方户口簿、近期合影照片3张（2寸）',
     '《中华人民共和国民法典》第一千零四十九条', '即时办理', '免费'),
    ('HY-002', '离婚登记', '婚姻生育', '民政部',
     '夫妻双方自愿离婚并已达成一致协议的离婚登记。',
     '双方身份证、户口簿、结婚证、离婚协议书（一式三份）',
     '《中华人民共和国民法典》第一千零七十六条', '30个工作日（冷静期）', '免费'),
    ('HY-003', '生育登记', '婚姻生育', '卫生部',
     '为生育子女的家庭办理生育登记服务。',
     '身份证、户口簿、结婚证、医院妊娠诊断证明',
     '《中华人民共和国人口与计划生育法》', '即时办理', '免费'),
    ('SH-001', '城乡居民基本医疗保险参保', '社会保障', '人社部',
     '未参加职工医保的城乡居民参加基本医疗保险。',
     '身份证、户口簿、参保登记表',
     '《中华人民共和国社会保险法》第二十四条', '5个工作日', '按年度标准缴费'),
    ('SH-002', '养老金领取资格认证', '社会保障', '人社部',
     '退休人员每年度的养老金领取资格认证。',
     '身份证、本人到场或人脸识别认证',
     '《中华人民共和国社会保险法》第十六条', '即时办理', '免费'),
    ('SH-003', '最低生活保障申请', '社会保障', '民政部',
     '家庭人均收入低于当地低保标准的家庭申请最低生活保障。',
     '身份证、户口簿、收入证明、家庭财产申报表、疾病/残疾证明（如有）',
     '《社会救助暂行办法》第十二条', '20个工作日', '免费'),
    ('SH-004', '失业登记', '社会保障', '人社部',
     '劳动年龄内有劳动能力且有就业意愿的失业人员登记。',
     '身份证、户口簿或居住证、解除劳动合同证明（如有）',
     '《就业促进法》第三十五条', '即时办理', '免费'),
    ('JY-001', '就业创业证申领', '就业创业', '人社部',
     '为就业、失业人员办理《就业创业证》。',
     '身份证、户口簿、近期免冠照片、学历证明（如有）',
     '《就业促进法》第三十五条', '7个工作日', '免费'),
    ('JY-002', '创业担保贷款申请', '就业创业', '财政部',
     '为符合条件的创业人员申请创业担保贷款贴息支持。',
     '身份证、营业执照、创业项目计划书、担保材料',
     '《国务院关于进一步做好稳就业工作的意见》', '15个工作日', '免费（贴息）'),
    ('ZJ-001', '无犯罪记录证明', '证件办理', '公安部',
     '为有需要的公民出具无犯罪记录证明，系统自动对接户籍系统核查。',
     '身份证、本人申请（他人代办需委托书及双方身份证）',
     '《中华人民共和国刑法》第一百条', '3个工作日', '免费'),
    ('ZJ-002', '社会保障卡申领', '证件办理', '人社部',
     '为参保人员申领社会保障卡（医保、养老等功能一卡通）。',
     '身份证、本人近期免冠照片、参保凭证',
     '《中华人民共和国社会保险法》第五十七条', '15个工作日', '首次免费'),
    ('ZJ-003', '残疾人证办理', '证件办理', '民政部',
     '为符合条件的残疾人申领残疾人证。',
     '身份证、户口簿、医院残疾评定证明、照片',
     '《中华人民共和国残疾人保障法》第四条', '20个工作日', '免费'),
    ('MZ-001', '临时救助申请', '民政救济', '民政部',
     '因突发性、紧迫性、临时性生活困难申请临时救助。',
     '身份证、户口簿、困难情况说明、突发事件相关证明',
     '《社会救助暂行办法》第四十七条', '10个工作日', '免费'),
    ('MZ-002', '特困人员供养申请', '民政救济', '民政部',
     '无劳动能力、无生活来源且无法定赡养扶养义务人的特困人员供养申请。',
     '身份证、户口簿、健康状况证明、家庭经济状况申报',
     '《社会救助暂行办法》第十四条', '20个工作日', '免费'),
    ('QY-001', '个体工商户登记', '企业服务', '商务部',
     '自然人从事工商业经营办理个体工商户设立登记。',
     '身份证、经营场所使用证明、名称预先核准通知书（如需）',
     '《个体工商户条例》第八条', '3个工作日', '免费'),
    ('QY-002', '食品经营许可申请', '企业服务', '商务部',
     '从事食品销售、餐饮服务等活动申请食品经营许可。',
     '营业执照、场所平面图、食品安全管理制度、健康证明',
     '《中华人民共和国食品安全法》第三十五条', '20个工作日', '免费'),
    ('ZH-001', '政府信息公开申请', '综合服务', '国务院',
     '公民、法人或其他组织依法申请获取政府信息。',
     '身份证、政府信息公开申请表',
     '《中华人民共和国政府信息公开条例》第二十七条', '20个工作日', '免费'),
    ('ZH-002', '政策咨询', '综合服务', '国务院',
     '就相关政策法规、办事流程等事项提出咨询。',
     '身份证',
     '《中华人民共和国政府信息公开条例》', '5个工作日', '免费'),
]

# ========== 数据库管理 ==========
class DB:
    @staticmethod
    def get_conn(db_path):
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=15000")
        except Exception:
            pass
        return conn

    @staticmethod
    def init_gov_db():
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        # ---- 原有表 ----
        c.execute('''CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT,
            real_name TEXT, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS officials (
            id INTEGER PRIMARY KEY AUTOINCREMENT, id_number TEXT UNIQUE, name TEXT, gender TEXT,
            position TEXT, department TEXT, rank TEXT, status TEXT DEFAULT '在职',
            appointed_date TEXT, phone TEXT, address TEXT, created_at TEXT, last_update TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS approval_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, content TEXT, file_type TEXT,
            applicant_id TEXT, applicant_name TEXT, applicant_type TEXT, status TEXT DEFAULT '待审批',
            approver_id TEXT, approver_name TEXT, approve_comment TEXT, created_at TEXT, approved_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id TEXT, reporter_name TEXT,
            official_id TEXT, official_name TEXT, title TEXT, content TEXT,
            status TEXT DEFAULT '待处理', handler TEXT, handle_result TEXT,
            created_at TEXT, handled_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, citizen_id TEXT, citizen_name TEXT,
            official_id TEXT, official_name TEXT, title TEXT, content TEXT,
            status TEXT DEFAULT '未回复', reply TEXT, replier TEXT,
            created_at TEXT, replied_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS lawsuits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_number TEXT, official_id TEXT, official_name TEXT,
            charge TEXT, description TEXT, status TEXT DEFAULT '已立案', court_case_id INTEGER,
            created_at TEXT, created_by TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS work_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, official_id TEXT, official_name TEXT,
            title TEXT, content TEXT, status TEXT DEFAULT '已提交',
            review_comment TEXT, reviewer TEXT, created_at TEXT, reviewed_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, content TEXT, publisher TEXT,
            priority TEXT DEFAULT '普通', created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, operator_type TEXT, operator_id TEXT, operator_name TEXT,
            action TEXT, details TEXT, ip TEXT, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, receiver_type TEXT, receiver_id TEXT,
            title TEXT, content TEXT, is_read INTEGER DEFAULT 0, created_at TEXT)''')

        # ---- 新增表：政务服务 ----
        # 办事事项（服务目录）
        c.execute('''CREATE TABLE IF NOT EXISTS service_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_code TEXT UNIQUE, name TEXT, category TEXT,
            department TEXT, description TEXT, required_materials TEXT, legal_basis TEXT,
            processing_days TEXT, fee TEXT, is_active INTEGER DEFAULT 1, created_at TEXT)''')
        # 办件（在线申办）
        c.execute('''CREATE TABLE IF NOT EXISTS service_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_no TEXT UNIQUE, item_id INTEGER, item_code TEXT,
            item_name TEXT, category TEXT, applicant_id TEXT, applicant_name TEXT,
            applicant_phone TEXT, applicant_address TEXT, department TEXT,
            handler_id TEXT, handler_name TEXT, content TEXT,
            status TEXT DEFAULT '待受理', progress TEXT, handle_comment TEXT,
            created_at TEXT, accepted_at TEXT, finished_at TEXT,
            rating INTEGER DEFAULT 0, rating_comment TEXT)''')
        # 办事预约
        c.execute('''CREATE TABLE IF NOT EXISTS service_appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, appt_no TEXT UNIQUE, item_id INTEGER, item_name TEXT,
            applicant_id TEXT, applicant_name TEXT, applicant_phone TEXT,
            appt_date TEXT, appt_time TEXT, department TEXT, handler_name TEXT,
            status TEXT DEFAULT '已预约', remark TEXT, created_at TEXT)''')
        # 政策文件（政务公开）
        c.execute('''CREATE TABLE IF NOT EXISTS policy_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_no TEXT, title TEXT, category TEXT,
            issuing_department TEXT, issue_date TEXT, content TEXT, publisher TEXT,
            is_active INTEGER DEFAULT 1, created_at TEXT)''')
        # 公文（内部收发文）
        c.execute('''CREATE TABLE IF NOT EXISTS official_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_no TEXT UNIQUE, title TEXT, doc_type TEXT,
            urgency TEXT, from_department TEXT, to_department TEXT, sender_id TEXT, sender_name TEXT,
            content TEXT, status TEXT DEFAULT '待签收', handler_name TEXT, handle_comment TEXT,
            created_at TEXT, handled_at TEXT)''')
        # 政务咨询
        c.execute('''CREATE TABLE IF NOT EXISTS consultations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, citizen_id TEXT, citizen_name TEXT,
            category TEXT, title TEXT, content TEXT, department TEXT,
            status TEXT DEFAULT '待回复', reply TEXT, replier TEXT,
            created_at TEXT, replied_at TEXT)''')
        # 总统行政令（总统签署/发布；政务公开展示；可撤回）
        c.execute('''CREATE TABLE IF NOT EXISTS executive_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT UNIQUE, title TEXT NOT NULL,
            content TEXT DEFAULT '', publisher_id INTEGER DEFAULT 0, publisher_name TEXT DEFAULT '',
            publisher_position TEXT DEFAULT '总统', effective_mode TEXT DEFAULT '立即生效',
            effective_time TEXT DEFAULT '', is_withdrawn INTEGER DEFAULT 0,
            withdrawn_at TEXT DEFAULT '', withdrawn_by TEXT DEFAULT '', created_at TEXT)''')

        # ---- 老表迁移 ----
        cols = [x[1] for x in c.execute('PRAGMA table_info(approval_files)').fetchall()]
        if 'target_department' not in cols:
            c.execute("ALTER TABLE approval_files ADD COLUMN target_department TEXT")

        # ========== 权限模块：部门 / 职级 / 官员个人覆盖 ==========
        # 部门：树形（parent_id）+ 权力分值 power_level（越大权力越大）
        c.execute('''CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, parent_id INTEGER DEFAULT 0,
            power_level INTEGER DEFAULT 50, head_name TEXT DEFAULT '', sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1, created_at TEXT)''')
        # 职级：11 级（grade 越大级别越高），各职级带“默认权限位”和默认审批范围
        c.execute('''CREATE TABLE IF NOT EXISTS rank_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, grade INTEGER UNIQUE,
            d_send_clause INTEGER DEFAULT 1, d_approve INTEGER DEFAULT 0, d_send_doc INTEGER DEFAULT 0,
            d_handle INTEGER DEFAULT 1, default_scope TEXT DEFAULT 'none', sort_order INTEGER DEFAULT 0)''')
        # 官员个人权限覆盖：mode=allow 强制允许 / deny 强制禁止（不写=沿用职级默认）
        c.execute('''CREATE TABLE IF NOT EXISTS official_perm_overrides (
            official_id INTEGER NOT NULL, perm_key TEXT NOT NULL, mode TEXT NOT NULL,
            PRIMARY KEY (official_id, perm_key))''')
        # 官员个人审批范围覆盖（不写=沿用职级默认范围）
        c.execute('''CREATE TABLE IF NOT EXISTS official_scope_overrides (
            official_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, custom_depts TEXT DEFAULT '')''')
        # 职务：每个职务可对四项权限设 default(跟随职级)/allow/deny；审批范围 default=跟随职级
        c.execute('''CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
            p_send_clause TEXT DEFAULT 'default', p_approve TEXT DEFAULT 'default',
            p_send_doc TEXT DEFAULT 'default', p_handle TEXT DEFAULT 'default',
            p_scope TEXT DEFAULT 'default', p_custom_depts TEXT DEFAULT '',
            department TEXT DEFAULT '',  -- 所属部门（空=通用职位）
            dept_kind TEXT DEFAULT '',   -- 部门类别：''/'legislative'/'judicial'/'state'
            sort_order INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, created_at TEXT)''')
        # 旧库补列：positions.department / dept_kind（已存在的表用 ALTER 迁移）
        try:
            c.execute("ALTER TABLE positions ADD COLUMN department TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE positions ADD COLUMN dept_kind TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

        # ========== 会议投票模块 ==========
        # 会议：召开人（总统/部长）可关联一份“需要其审批”的文件（可选）
        c.execute('''CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL, content TEXT DEFAULT '',
            convener_id INTEGER NOT NULL, convener_name TEXT DEFAULT '',
            approval_id INTEGER, approval_title TEXT DEFAULT '',
            status TEXT DEFAULT '投票中',
            yes_count INTEGER DEFAULT 0, no_count INTEGER DEFAULT 0, abstain_count INTEGER DEFAULT 0,
            total_count INTEGER DEFAULT 0,
            final_decision TEXT DEFAULT '', decision_comment TEXT DEFAULT '', decided_at TEXT DEFAULT '',
            created_at TEXT, closed_at TEXT DEFAULT '')''')
        # 参会人员（在职官员）
        c.execute('''CREATE TABLE IF NOT EXISTS meeting_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id INTEGER NOT NULL, official_id INTEGER NOT NULL,
            UNIQUE(meeting_id, official_id))''')
        # 投票记录：每名参会人每场会议仅可投一次
        c.execute('''CREATE TABLE IF NOT EXISTS meeting_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id INTEGER NOT NULL, official_id INTEGER NOT NULL,
            voter_name TEXT DEFAULT '', option TEXT NOT NULL, comment TEXT DEFAULT '', created_at TEXT,
            UNIQUE(meeting_id, official_id))''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_mp_meeting ON meeting_participants(meeting_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mv_meeting ON meeting_votes(meeting_id)")

        # ========== V2 美式联邦制：新表 ==========
        # 权力点目录（44项左右：行政/立法/司法/党派/州权/通用）
        c.execute('''CREATE TABLE IF NOT EXISTS power_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pkey TEXT UNIQUE, name TEXT NOT NULL,
            branch TEXT DEFAULT '其他', description TEXT DEFAULT '', sort_order INTEGER DEFAULT 0)''')
        # 职级默认授予的权力（value=1 授予）
        c.execute('''CREATE TABLE IF NOT EXISTS rank_powers (
            rank_id INTEGER NOT NULL, power_key TEXT NOT NULL, value INTEGER DEFAULT 0,
            PRIMARY KEY (rank_id, power_key))''')
        # 职务对权力的覆盖（mode=default/allow/deny）
        c.execute('''CREATE TABLE IF NOT EXISTS position_powers (
            position_id INTEGER NOT NULL, power_key TEXT NOT NULL, mode TEXT DEFAULT 'default',
            PRIMARY KEY (position_id, power_key))''')
        # 党派
        c.execute('''CREATE TABLE IF NOT EXISTS parties (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, short_name TEXT DEFAULT '',
            leader_id INTEGER DEFAULT 0, leader_name TEXT DEFAULT '', platform TEXT DEFAULT '',
            created_at TEXT, is_active INTEGER DEFAULT 1)''')
        # 党员（在职官员加入党派）
        c.execute('''CREATE TABLE IF NOT EXISTS party_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER NOT NULL, official_id INTEGER NOT NULL,
            official_name TEXT DEFAULT '', joined_at TEXT, UNIQUE(party_id, official_id))''')
        # 入党申请（待党魁审批；同一人同一党仅一条待审批）
        c.execute('''CREATE TABLE IF NOT EXISTS party_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER NOT NULL,
            official_id INTEGER NOT NULL, official_name TEXT DEFAULT '',
            status TEXT DEFAULT '待审批',  -- 待审批/已批准/已拒绝
            note TEXT DEFAULT '', created_at TEXT, decided_at TEXT DEFAULT '')''')
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pr_pending ON party_requests(party_id, official_id) WHERE status='待审批'")
        # 总统大选
        c.execute('''CREATE TABLE IF NOT EXISTS elections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT DEFAULT '总统大选',
            status TEXT DEFAULT '筹备中',  -- 筹备中/候选人登记/投票中/已结束
            election_type TEXT DEFAULT 'regular',  -- regular 定期大选 / by 补选
            reason TEXT DEFAULT '',       -- 补选原因：弹劾定罪/总统丧失履职能力/国家重大危机
            term_start TEXT DEFAULT '', term_end TEXT DEFAULT '',  -- 当选者任期
            started_by TEXT DEFAULT '', started_by_name TEXT DEFAULT '',
            created_at TEXT, voting_started_at TEXT DEFAULT '', ended_at TEXT DEFAULT '',
            winner_id INTEGER DEFAULT 0, winner_name TEXT DEFAULT '', winner_party TEXT DEFAULT '')''')
        # 旧库补列：elections.election_type / reason / term_start / term_end
        for _col in ('election_type', 'reason', 'term_start', 'term_end'):
            try:
                c.execute("ALTER TABLE elections ADD COLUMN %s TEXT DEFAULT ''" % _col)
            except sqlite3.OperationalError:
                pass
        # V3 换届制：计票日 / 就职日 / 是否已就职 / 当选副总统（搭档）
        for _col, _dft in (('close_at', "TEXT DEFAULT ''"), ('inauguration_date', "TEXT DEFAULT ''"),
                           ('inaugurated', "INTEGER DEFAULT 0"), ('inaugurated_at', "TEXT DEFAULT ''"),
                           ('vp_id', "INTEGER DEFAULT 0"), ('vp_name', "TEXT DEFAULT ''"),
                           ('vp_id_number', "TEXT DEFAULT ''")):
            try:
                c.execute("ALTER TABLE elections ADD COLUMN %s %s" % (_col, _dft))
            except sqlite3.OperationalError:
                pass
        # 大选候选人（党派推举）
        c.execute('''CREATE TABLE IF NOT EXISTS election_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, election_id INTEGER NOT NULL,
            party_id INTEGER DEFAULT 0, party_name TEXT DEFAULT '',
            official_id INTEGER NOT NULL, official_name TEXT DEFAULT '',
            platform TEXT DEFAULT '', created_at TEXT, UNIQUE(election_id, official_id))''')
        # V3 副总统搭档（候选人竞选绑定，户籍身份）
        for _col, _dft in (('partner_id_number', "TEXT DEFAULT ''"), ('partner_name', "TEXT DEFAULT ''")):
            try:
                c.execute("ALTER TABLE election_candidates ADD COLUMN %s %s" % (_col, _dft))
            except sqlite3.OperationalError:
                pass
        # 公民投票（一人一票：election_id + citizen_id_number 唯一）
        c.execute('''CREATE TABLE IF NOT EXISTS election_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, election_id INTEGER NOT NULL,
            citizen_id_number TEXT NOT NULL, citizen_name TEXT DEFAULT '',
            candidate_id INTEGER NOT NULL, voted_at TEXT,
            UNIQUE(election_id, citizen_id_number))''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_ev_election ON election_votes(election_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ec_election ON election_candidates(election_id)")

        # ============ 6 大新模块：国库/违宪审查/媒体事件/民调/请愿公投 ============
        # 1) 国库预算（账户本体在 bank_system.db.customers）
        c.execute('''CREATE TABLE IF NOT EXISTS budget_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, year INTEGER NOT NULL,
            department TEXT NOT NULL, allocated REAL DEFAULT 0, spent REAL DEFAULT 0,
            status TEXT DEFAULT '草案', created_at TEXT, approved_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS treasury_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, txn_type TEXT NOT NULL,
            amount REAL NOT NULL, balance_after REAL DEFAULT 0,
            from_card TEXT DEFAULT '', to_card TEXT DEFAULT '',
            note TEXT DEFAULT '', created_at TEXT)''')
        # 2) 司法违宪审查
        c.execute('''CREATE TABLE IF NOT EXISTS constitutional_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_no TEXT UNIQUE,
            target_type TEXT NOT NULL, target_id INTEGER NOT NULL,
            plaintiff TEXT DEFAULT '', plaintiff_id_number TEXT DEFAULT '',
            reason TEXT DEFAULT '', status TEXT DEFAULT '待审理',
            ruling TEXT DEFAULT '', ruling_note TEXT DEFAULT '',
            filed_at TEXT, ruled_at TEXT, justice_id INTEGER DEFAULT 0)''')
        # 3) 媒体事件引擎 + 各州民调
        c.execute('''CREATE TABLE IF NOT EXISTS news_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            content TEXT DEFAULT '', category TEXT DEFAULT '综合',
            impact_direction TEXT DEFAULT '中性', impact_value REAL DEFAULT 0,
            affected_state TEXT DEFAULT '全国', created_at TEXT,
            is_active INTEGER DEFAULT 1)''')
        c.execute('''CREATE TABLE IF NOT EXISTS state_polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT NOT NULL,
            candidate_id INTEGER NOT NULL, support_rate REAL DEFAULT 50,
            updated_at TEXT, UNIQUE(state, candidate_id))''')
        # 4) 公民请愿 + 公投
        c.execute('''CREATE TABLE IF NOT EXISTS petitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            description TEXT DEFAULT '', sponsor_id_number TEXT DEFAULT '',
            sponsor_name TEXT DEFAULT '', signatures_needed INTEGER DEFAULT 500,
            signatures_count INTEGER DEFAULT 0, status TEXT DEFAULT '收集签名',
            created_at TEXT, referred_referendum_id INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS petition_signatures (
            id INTEGER PRIMARY KEY AUTOINCREMENT, petition_id INTEGER NOT NULL,
            citizen_id_number TEXT NOT NULL, citizen_name TEXT DEFAULT '',
            signed_at TEXT, UNIQUE(petition_id, citizen_id_number))''')
        c.execute('''CREATE TABLE IF NOT EXISTS referenda (
            id INTEGER PRIMARY KEY AUTOINCREMENT, petition_id INTEGER DEFAULT 0,
            title TEXT NOT NULL, description TEXT DEFAULT '',
            ref_type TEXT DEFAULT 'other',  -- amend_constitution / recall_president / veto_law / other
            ref_target_id INTEGER DEFAULT 0,
            status TEXT DEFAULT '投票中', yes_votes INTEGER DEFAULT 0, no_votes INTEGER DEFAULT 0,
            threshold REAL DEFAULT 0.5, result TEXT DEFAULT '',
            created_at TEXT, closed_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS referendum_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, referendum_id INTEGER NOT NULL,
            citizen_id_number TEXT NOT NULL, citizen_name TEXT DEFAULT '',
            choice TEXT DEFAULT '赞成', voted_at TEXT,
            UNIQUE(referendum_id, citizen_id_number))''')


        # ---- 官员档案（履历）：历任职务/任期 + 大选当选/落选记录 ----
        # 旧版表级 UNIQUE(election_id,official_id,record_type) 会阻断同一官员多条“任职/卸任”履历
        # （任命调整/连任/自动卸任都需要追加多条非竞选履历），检测到旧结构则重建；
        # 旧竞选履历由启动补齐（backfill_election_careers）按大选记录自动恢复。
        _career_sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='official_careers'").fetchone()
        if _career_sql and 'UNIQUE(election_id, official_id, record_type)' in (_career_sql[0] or ''):
            c.execute("DROP TABLE official_careers")
        c.execute('''CREATE TABLE IF NOT EXISTS official_careers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            election_id INTEGER DEFAULT 0,   -- 关联大选（0=非竞选产生的履历）
            official_id INTEGER NOT NULL,
            record_type TEXT DEFAULT '任职',  -- 任职 / 当选 / 落选 / 连任 / 卸任
            position TEXT DEFAULT '',         -- 职务（竞选记录=竞选职务）
            department TEXT DEFAULT '',
            term_start TEXT DEFAULT '', term_end TEXT DEFAULT '',
            note TEXT DEFAULT '', created_at TEXT)''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_oc_official ON official_careers(official_id)")
        # 竞选履历仍保持唯一（防重复计档），非竞选履历（election_id=0）允许多条
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_oc_uq ON official_careers(election_id, official_id, record_type) "
                  "WHERE election_id > 0")

        # ---- 官员任期扩展列：起任日/卸任日（非选任职务由上级任命确定）+ 连任自动提交开关 ----
        for _col, _ddl in (('term_start', "ALTER TABLE officials ADD COLUMN term_start TEXT DEFAULT ''"),
                           ('term_end', "ALTER TABLE officials ADD COLUMN term_end TEXT DEFAULT ''"),
                           ('auto_renewal', "ALTER TABLE officials ADD COLUMN auto_renewal INTEGER DEFAULT 1"),
                           ('photo', "ALTER TABLE officials ADD COLUMN photo TEXT DEFAULT ''"),
                           ('intro', "ALTER TABLE officials ADD COLUMN intro TEXT DEFAULT ''"),
                           ('party', "ALTER TABLE officials ADD COLUMN party TEXT DEFAULT ''")):
            try:
                c.execute(_ddl)
            except Exception:
                pass
        # 现任非选任官员默认任期：自系统时间（2026-10-31）起至 2028-10-30，到期自动卸任
        c.execute('''UPDATE officials SET term_start=CASE WHEN term_start IS NULL OR term_start='' THEN '2026-10-31' ELSE term_start END,
                     term_end=CASE WHEN term_end IS NULL OR term_end='' THEN '2028-10-30' ELSE term_end END
                     WHERE status='在职' AND position NOT IN ('总统','副总统')''')

        # ---- 任职协议：任命/调整职务后首次登录须签署，同意后方可进行官员操作 ----
        c.execute('''CREATE TABLE IF NOT EXISTS career_agreements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, official_id INTEGER NOT NULL,
            position TEXT, department TEXT, term_start TEXT, term_end TEXT,
            agreed INTEGER DEFAULT 0, agreed_at TEXT, created_at TEXT)''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_ca_official ON career_agreements(official_id)")

        # ---- 连任申请：任期结束前30天提交，上级审核；前10天自动提交；卸任前3天无人受理自动通过 ----
        c.execute('''CREATE TABLE IF NOT EXISTS renewal_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, official_id INTEGER NOT NULL,
            official_name TEXT, position TEXT, department TEXT,
            old_term_start TEXT, old_term_end TEXT, new_term_end TEXT,
            status TEXT DEFAULT '待审核',       -- 待审核 / 已通过 / 已驳回 / 自动通过 / 自动提交
            submit_method TEXT DEFAULT '手动',   -- 手动 / 自动
            submitted_at TEXT, reviewed_by TEXT, reviewed_at TEXT, review_note TEXT)''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_ra_official ON renewal_applications(official_id)")

        # ---- 合同管理：上级对单个/多个官员发起合同，官员签署 ----
        c.execute('''CREATE TABLE IF NOT EXISTS gov_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, content TEXT,
            issuer_id INTEGER, issuer_name TEXT, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS contract_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id INTEGER NOT NULL,
            official_id INTEGER NOT NULL, official_name TEXT,
            status TEXT DEFAULT '待签署',   -- 待签署 / 已签署 / 已拒绝
            signed_at TEXT, created_at TEXT)''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_ct_contract ON contract_targets(contract_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ct_official ON contract_targets(official_id)")

        # ---- 修宪模块：宪法档案 + 修宪审议 ----
        c.execute('''CREATE TABLE IF NOT EXISTS constitution (
            id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER DEFAULT 1,
            title TEXT DEFAULT '蛋国宪法', items TEXT DEFAULT '',  -- 每条一行
            status TEXT DEFAULT '生效中', passed_at TEXT, updated_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS constitutional_amendments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            content TEXT DEFAULT '',  -- 修正条文，每条一行
            proposer_id INTEGER DEFAULT 0, proposer_name TEXT DEFAULT '',
            status TEXT DEFAULT '待审议',  -- 待审议/审议中/已通过/已否决
            yes_count INTEGER DEFAULT 0, no_count INTEGER DEFAULT 0, abstain_count INTEGER DEFAULT 0,
            created_at TEXT, reviewed_at TEXT DEFAULT '')''')
        c.execute('''CREATE TABLE IF NOT EXISTS amendment_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, amendment_id INTEGER NOT NULL,
            official_id INTEGER NOT NULL, official_name TEXT DEFAULT '',
            vote TEXT DEFAULT '',  -- 赞成/反对/弃权
            voted_at TEXT, UNIQUE(amendment_id, official_id))''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_amv ON amendment_votes(amendment_id)")

        # ---- 弹劾模块：弹劾案 + 议会表决 ----
        c.execute('''CREATE TABLE IF NOT EXISTS impeachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER DEFAULT 0, target_name TEXT DEFAULT '',
            reason TEXT DEFAULT '',  -- 弹劾理由
            initiator_id INTEGER DEFAULT 0, initiator_name TEXT DEFAULT '',
            status TEXT DEFAULT '进行中',  -- 进行中/已通过/未通过/已撤销
            yes_count INTEGER DEFAULT 0, no_count INTEGER DEFAULT 0, abstain_count INTEGER DEFAULT 0,
            created_at TEXT, reviewed_at TEXT DEFAULT '')''')
        c.execute('''CREATE TABLE IF NOT EXISTS impeachment_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, impeachment_id INTEGER NOT NULL,
            official_id INTEGER NOT NULL, official_name TEXT DEFAULT '',
            vote TEXT DEFAULT '',  -- 赞成/反对/弃权
            voted_at TEXT, UNIQUE(impeachment_id, official_id))''')

        # ---- 法律发布：总统起草 → 议员投票 → 到生效日总统签发行政令发布 ----
        c.execute('''CREATE TABLE IF NOT EXISTS laws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            proposer_id INTEGER DEFAULT 0,
            proposer_name TEXT DEFAULT '',
            vote_deadline TEXT DEFAULT '',   -- 最后审批时间（到点自动计票）
            effective_date TEXT DEFAULT '',  -- 生效日期
            status TEXT DEFAULT '投票中',   -- 投票中/已通过待发布/已生效/未通过
            yes_count INTEGER DEFAULT 0,
            no_count INTEGER DEFAULT 0,
            abstain_count INTEGER DEFAULT 0,
            is_amendment_of INTEGER DEFAULT 0,  -- 修改某法律时指向原法律 id
            admin_order_id INTEGER DEFAULT 0,   -- 发布后签发的行政令 id
            created_at TEXT,
            voted_at TEXT DEFAULT '',
            published_at TEXT DEFAULT '')''')
        c.execute('''CREATE TABLE IF NOT EXISTS law_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            law_id INTEGER NOT NULL,
            official_id INTEGER NOT NULL,
            official_name TEXT DEFAULT '',
            vote TEXT DEFAULT '',  -- 赞成/反对/弃权
            voted_at TEXT,
            UNIQUE(law_id, official_id))''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_law ON law_votes(law_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_impv ON impeachment_votes(impeachment_id)")

        # ---- 公职申请（普通民众申请政府职位） ----
        c.execute('''CREATE TABLE IF NOT EXISTS public_job_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applicant_id_number TEXT NOT NULL,
            applicant_name TEXT NOT NULL,
            department TEXT DEFAULT '',
            position_name TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            status TEXT DEFAULT '待审核',
            reviewer_id INTEGER DEFAULT 0,
            reviewer_name TEXT DEFAULT '',
            reviewer_note TEXT DEFAULT '',
            reviewed_at TEXT DEFAULT '',
            term_start TEXT DEFAULT '',
            term_end TEXT DEFAULT '',
            official_id INTEGER DEFAULT 0,
            created_at TEXT)''')

        # ---- 国籍申请 ----
        c.execute('''CREATE TABLE IF NOT EXISTS nationality_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applicant_name TEXT NOT NULL,
            original_nationality TEXT DEFAULT '',
            birth_date TEXT DEFAULT '',
            gender TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            foreign_passport_no TEXT DEFAULT '',
            temp_account TEXT UNIQUE,
            temp_password TEXT DEFAULT '',
            status TEXT DEFAULT '待审核',
            assigned_id_number TEXT DEFAULT '',
            assigned_address TEXT DEFAULT '蛋国首都中央区',
            reviewer_id INTEGER DEFAULT 0,
            reviewer_name TEXT DEFAULT '',
            reviewer_note TEXT DEFAULT '',
            reviewed_at TEXT DEFAULT '',
            confirmed_at TEXT DEFAULT '',
            used INTEGER DEFAULT 0,
            created_at TEXT)''')

        # ---- 国籍证明防伪编号（每开一份证明登记一条，供扫码查验）----
        c.execute('''CREATE TABLE IF NOT EXISTS certificate_serials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            serial TEXT UNIQUE,
            id_number TEXT,
            name TEXT,
            issued_at TEXT)''')

        # ---- 国籍申请扩展列：补充材料要求/提交、申请人脸照 ----
        for _col, _dft in (('requested_materials', "TEXT DEFAULT ''"),
                           ('materials_status', "TEXT DEFAULT ''"),
                           ('submitted_materials', "TEXT DEFAULT ''"),
                           ('materials_at', "TEXT DEFAULT ''"),
                           ('face_b64', "TEXT DEFAULT ''")):
            try:
                c.execute("ALTER TABLE nationality_applications ADD COLUMN %s %s" % (_col, _dft))
            except sqlite3.OperationalError:
                pass

        # ---- 国籍申请补充材料附件（按审核人列明的要求逐项上传，文件本体存 b64）----
        c.execute('''CREATE TABLE IF NOT EXISTS nationality_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_id INTEGER NOT NULL,
            req_item TEXT DEFAULT '',
            file_name TEXT DEFAULT '',
            file_ext TEXT DEFAULT '',
            mime TEXT DEFAULT '',
            data_b64 TEXT,
            uploaded_at TEXT)''')

        # ---- 户籍库人脸表补 face_b64 列（人脸照片上云，供入籍申请比对）----
        try:
            hc = DB.get_conn(HOUSEHOLD_DB)
            hc.execute("ALTER TABLE face_info ADD COLUMN face_b64 TEXT DEFAULT ''")
            hc.commit()
            hc.close()
        except Exception:
            pass

        # ---- 法院判决表补"剥夺政治权利"两列（政务侧硬校验要读）----
        try:
            cc = DB.get_conn(COURT_DB)
            for _c, _d in (('deprive_political_defendant', 'INTEGER DEFAULT 0'),
                           ('deprive_political_plaintiff', 'INTEGER DEFAULT 0'),
                           ('deprive_defendant_id', "TEXT DEFAULT ''"),
                           ('deprive_plaintiff_id', "TEXT DEFAULT ''")):
                try:
                    cc.execute("ALTER TABLE judgments ADD COLUMN %s %s" % (_c, _d))
                except Exception:
                    pass
            cc.commit()
            cc.close()
        except Exception:
            pass

        # ---- 总统竞选申请（官员/公民均可自荐，管理后台审核） ----
        c.execute('''CREATE TABLE IF NOT EXISTS candidacy_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applicant_type TEXT DEFAULT 'citizen',  -- official/citizen
            applicant_id INTEGER DEFAULT 0,         -- officials.id（公民审核通过后建档）
            id_number TEXT NOT NULL, name TEXT NOT NULL,
            party_id INTEGER DEFAULT 0, party_name TEXT DEFAULT '',
            platform TEXT DEFAULT '',  -- 竞选申请书/纲领
            status TEXT DEFAULT '待审核',  -- 待审核/已通过/已拒绝
            election_id INTEGER DEFAULT 0,
            created_at TEXT, reviewed_at TEXT DEFAULT '')''')
        # V3 副总统搭档（自荐参选时绑定，户籍身份）
        for _col, _dft in (('partner_id_number', "TEXT DEFAULT ''"), ('partner_name', "TEXT DEFAULT ''")):
            try:
                c.execute("ALTER TABLE candidacy_applications ADD COLUMN %s %s" % (_col, _dft))
            except sqlite3.OperationalError:
                pass
        # ---- 官员离职申请（提交后由上级审核；通过则办理离职） ----
        c.execute('''CREATE TABLE IF NOT EXISTS resignations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            official_id INTEGER NOT NULL, official_name TEXT DEFAULT '',
            department TEXT DEFAULT '', position TEXT DEFAULT '',
            reason TEXT DEFAULT '',  -- 离职原因
            status TEXT DEFAULT '待审核',  -- 待审核/已同意/已驳回
            created_at TEXT, reviewed_at TEXT DEFAULT '',
            reviewer_id INTEGER DEFAULT 0, reviewer_name TEXT DEFAULT '',
            review_note TEXT DEFAULT '')''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_resig ON resignations(official_id)")
        # ---- 党派：公民入党支持（party_requests/party_members 兼容官员与公民） ----
        for _tbl in ('party_requests', 'party_members'):
            for _col, _dft in (('applicant_type', "TEXT DEFAULT 'official'"),
                               ('citizen_id_number', "TEXT DEFAULT ''"),
                               ('citizen_name', "TEXT DEFAULT ''")):
                try:
                    c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (_tbl, _col, _dft))
                except sqlite3.OperationalError:
                    pass
        # 重建待审批唯一索引：官员按 official_id、公民按 citizen_id_number 各自唯一
        try:
            c.execute("DROP INDEX IF EXISTS idx_pr_pending")
        except Exception:
            pass
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pr_pending "
                  "ON party_requests(party_id, official_id, citizen_id_number) WHERE status='待审批'")

        # ---- 预置部门 / 职级 / 职务（V2 美式体系；空库初始化，旧库走迁移） ----
        dept_cnt = c.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        pos_cnt = c.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        rank_cnt = c.execute("SELECT COUNT(*) FROM rank_levels").fetchone()[0]
        now0 = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if dept_cnt == 0:
            # 全新库：按 V2 常量建立部门树
            # 行政分支：总统府(顶层) -> 内阁15部 + 独立机构
            c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                      "VALUES (?,0,1000,0,1,?)", ('总统府', now0))
            exec_top = c.lastrowid
            cabinet = ['国务院', '财政部', '国防部', '司法部', '内政部', '农业部', '商务部',
                       '劳工部', '卫生与公众服务部', '住房与城市发展部', '交通部', '能源部',
                       '教育部', '退伍军人事务部', '国土安全部']
            for i, dn in enumerate(cabinet, start=1):
                c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                          "VALUES (?,?,90,?,1,?)", (dn, exec_top, i, now0))
            agencies = ['中央情报局', '联邦调查局', '环境保护署', '国家央行']
            for i, dn in enumerate(agencies, start=20):
                c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                          "VALUES (?,?,70,?,1,?)", (dn, exec_top, i, now0))
            # 立法分支：议会(顶层) -> 参议院/众议院
            c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                      "VALUES (?,0,800,30,1,?)", ('议会', now0))
            leg_top = c.lastrowid
            for i, dn in enumerate(['参议院', '众议院'], start=1):
                c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                          "VALUES (?,?,400,?,1,?)", (dn, leg_top, i, now0))
            # 司法分支：最高法院(顶层) -> 联邦上诉法院/联邦地区法院
            c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                      "VALUES (?,0,800,40,1,?)", ('最高法院', now0))
            jud_top = c.lastrowid
            for i, dn in enumerate(['联邦上诉法院', '联邦地区法院'], start=1):
                c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                          "VALUES (?,?,300,?,1,?)", (dn, jud_top, i, now0))
            # 州：6 个州（顶层，联邦制州自治）
            for i, dn in enumerate(['海文州', '维拉州', '奥林州', '凯林州', '米伦州', '索伦州'], start=1):
                c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                          "VALUES (?,0,50,?,1,?)", (dn, 50 + i, now0))
            # 国籍管理部门（顶层）
            c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                      "VALUES (?,0,60,90,1,?)", ('国籍管理部门', now0))
        else:
            DB._migrate_departments_v2(c)

        # ---- 职级：V2 21 档（GS-1~GS-15 < SES < ES-5~ES-1，grade 越大越高） ----
        if rank_cnt == 0:
            _rank_seed = [
                # (名称, 默认发送条款, 默认审批, 默认发文, 默认办件, 默认审批范围)
                ('GS-1级文官', 1, 0, 0, 1, 'none'),
                ('GS-2级文官', 1, 0, 0, 1, 'none'),
                ('GS-3级文官', 1, 0, 0, 1, 'none'),
                ('GS-4级文官', 1, 0, 0, 1, 'none'),
                ('GS-5级文官', 1, 0, 0, 1, 'none'),
                ('GS-6级文官', 1, 0, 0, 1, 'none'),
                ('GS-7级文官', 1, 0, 1, 1, 'none'),
                ('GS-8级文官', 1, 0, 1, 1, 'none'),
                ('GS-9级文官', 1, 0, 1, 1, 'self'),
                ('GS-10级文官', 1, 1, 1, 1, 'self'),
                ('GS-11级文官', 1, 1, 1, 1, 'self'),
                ('GS-12级文官', 1, 1, 1, 1, 'self'),
                ('GS-13级文官', 1, 1, 1, 1, 'subtree'),
                ('GS-14级文官', 1, 1, 1, 1, 'subtree'),
                ('GS-15级文官', 1, 1, 1, 1, 'subtree'),
                ('高级行政官员(SES)', 1, 1, 1, 1, 'subtree'),
                ('ES-5级政治任命', 1, 1, 1, 1, 'all'),
                ('ES-4级政治任命', 1, 1, 1, 1, 'all'),
                ('ES-3级政治任命', 1, 1, 1, 1, 'all'),
                ('ES-2级政治任命', 1, 1, 1, 1, 'all'),
                ('ES-1级政治任命', 1, 1, 1, 1, 'all'),
            ]
            for i, (rn, sc, ap, sd, hd, scope) in enumerate(_rank_seed, start=1):
                c.execute('''INSERT INTO rank_levels
                    (name,grade,d_send_clause,d_approve,d_send_doc,d_handle,default_scope,sort_order)
                    VALUES (?,?,?,?,?,?,?,?)''',
                          (rn, i, sc, ap, sd, hd, scope, i))
        else:
            DB._migrate_ranks_v2(c)

        # ---- 职务：V2 全美式官方名（带所属部门；空部门=通用职位） ----
        if pos_cnt == 0:
            _pos_seed = [
                # (名称, 发送条款, 审批, 发文, 办件, 审批范围, 所属部门, 部门类别)
                # 国家元首/政府首脑
                ('总统', 'allow', 'allow', 'allow', 'allow', 'all', '总统府', ''),
                ('副总统', 'allow', 'allow', 'allow', 'allow', 'all', '总统府', ''),
                # 内阁部长
                ('国务卿', 'allow', 'allow', 'allow', 'allow', 'all', '国务院', ''),
                ('财政部长', 'allow', 'allow', 'allow', 'allow', 'all', '财政部', ''),
                ('国防部长', 'allow', 'allow', 'allow', 'allow', 'all', '国防部', ''),
                ('司法部长', 'allow', 'allow', 'allow', 'allow', 'all', '司法部', ''),
                ('内政部长', 'allow', 'allow', 'allow', 'allow', 'all', '内政部', ''),
                ('农业部长', 'allow', 'allow', 'allow', 'allow', 'all', '农业部', ''),
                ('商务部长', 'allow', 'allow', 'allow', 'allow', 'all', '商务部', ''),
                ('劳工部长', 'allow', 'allow', 'allow', 'allow', 'all', '劳工部', ''),
                ('卫生与公众服务部长', 'allow', 'allow', 'allow', 'allow', 'all', '卫生与公众服务部', ''),
                ('住房与城市发展部长', 'allow', 'allow', 'allow', 'allow', 'all', '住房与城市发展部', ''),
                ('交通部长', 'allow', 'allow', 'allow', 'allow', 'all', '交通部', ''),
                ('能源部长', 'allow', 'allow', 'allow', 'allow', 'all', '能源部', ''),
                ('教育部长', 'allow', 'allow', 'allow', 'allow', 'all', '教育部', ''),
                ('退伍军人事务部长', 'allow', 'allow', 'allow', 'allow', 'all', '退伍军人事务部', ''),
                ('国土安全部长', 'allow', 'allow', 'allow', 'allow', 'all', '国土安全部', ''),
                # 独立机构首长
                ('中央情报局局长', 'allow', 'allow', 'allow', 'allow', 'all', '中央情报局', ''),
                ('联邦调查局局长', 'allow', 'allow', 'allow', 'allow', 'all', '联邦调查局', ''),
                ('环境保护署署长', 'allow', 'allow', 'allow', 'allow', 'all', '环境保护署', ''),
                ('国家央行行长', 'allow', 'allow', 'allow', 'allow', 'all', '国家央行', ''),
                # 立法
                ('参议院议长', 'allow', 'allow', 'allow', 'allow', 'all', '议会', 'legislative'),
                ('众议院议长', 'allow', 'allow', 'allow', 'allow', 'all', '议会', 'legislative'),
                ('参议员', 'allow', 'allow', 'allow', 'allow', 'all', '议会', 'legislative'),
                ('众议员', 'allow', 'allow', 'allow', 'allow', 'all', '议会', 'legislative'),
                # 司法
                ('最高法院首席大法官', 'allow', 'allow', 'allow', 'allow', 'all', '最高法院', 'judicial'),
                ('最高法院大法官', 'allow', 'allow', 'allow', 'allow', 'all', '最高法院', 'judicial'),
                ('联邦上诉法官', 'allow', 'allow', 'allow', 'allow', 'all', '联邦上诉法院', 'judicial'),
                ('联邦地区法官', 'allow', 'allow', 'allow', 'allow', 'all', '联邦地区法院', 'judicial'),
                # 州级（各州通用，仅限州部门）
                ('州长', 'allow', 'allow', 'allow', 'allow', 'subtree', '', 'state'),
                ('副州长', 'allow', 'allow', 'allow', 'allow', 'self', '', 'state'),
                ('州议员', 'allow', 'allow', 'allow', 'allow', 'self', '', 'state'),
                # 职业文官/部门内设（通用）
                ('高级行政官员', 'default', 'default', 'default', 'default', 'default', '', ''),
                ('司长', 'default', 'default', 'default', 'default', 'default', '', ''),
                ('副司长', 'default', 'default', 'default', 'default', 'default', '', ''),
                ('处长', 'default', 'default', 'default', 'default', 'default', '', ''),
                ('副处长', 'default', 'default', 'default', 'default', 'default', '', ''),
                ('科长', 'default', 'default', 'default', 'default', 'default', '', ''),
                ('副科长', 'default', 'default', 'default', 'default', 'default', '', ''),
                ('办事员', 'default', 'default', 'default', 'default', 'default', '', ''),
            ]
            for i, (pn, sc, ap, sd, hd, scope, dept, kind) in enumerate(_pos_seed):
                c.execute('''INSERT INTO positions
                    (name,p_send_clause,p_approve,p_send_doc,p_handle,p_scope,p_custom_depts,
                     department,dept_kind,sort_order,is_active,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (pn, sc, ap, sd, hd, scope, '', dept, kind, i, 1, now0))
        else:
            DB._migrate_positions_v2(c)
        # 幂等填充职务所属部门/类别（兼容真实库已存在的 40 个职务）
        DB._fill_positions_dept(c)
        # 总统/副总统职务默认授予“官员任免”权力；总统另授“签发行政令”（幂等；存量库同样生效）
        for _pname in ('总统', '副总统'):
            _pp = c.execute("SELECT id FROM positions WHERE name=?", (_pname,)).fetchone()
            if _pp:
                c.execute("INSERT OR IGNORE INTO position_powers (position_id,power_key,mode) VALUES (?,?,?)",
                          (_pp['id'], 'appoint_official', 'allow'))
        _pp = c.execute("SELECT id FROM positions WHERE name='总统'").fetchone()
        if _pp:
            c.execute("INSERT OR IGNORE INTO position_powers (position_id,power_key,mode) VALUES (?,?,?)",
                      (_pp['id'], 'exec_order', 'allow'))

        # ---- 权力点目录（44 项）----
        _power_seed = [
            # 行政权
            ('send_clause', '发送条款（起草呈批件/条款送审）', BRANCH_ADMIN, '行政发文前置权限', 1),
            ('approve', '审批决定（对待办通过/驳回）', BRANCH_ADMIN, '部门业务审批', 2),
            ('send_doc', '公文发文（对外/跨部门发文）', BRANCH_ADMIN, '对外公文发布', 3),
            ('handle', '办件办理与互动', BRANCH_ADMIN, '办理民众办件与互动', 4),
            ('exec_order', '签发行政令', BRANCH_ADMIN, '总统/州长签发行政命令', 5),
            ('veto', '总统否决权', BRANCH_ADMIN, '对议会法案行使否决', 6),
            ('appoint_official', '任免官员', BRANCH_ADMIN, '任命/撤换政府官员', 7),
            ('diplomacy', '外交与条约', BRANCH_ADMIN, '对外交往与缔结条约', 8),
            ('commander', '军队统帅', BRANCH_ADMIN, '三军统帅权', 9),
            ('pardon', '特赦权', BRANCH_ADMIN, '对罪犯实行特赦', 10),
            ('manage_dept', '部门业务管理', BRANCH_ADMIN, '管理本部门全部业务', 11),
            ('emergency', '宣布紧急状态', BRANCH_ADMIN, '宣布国家进入紧急状态', 12),
            ('commute', '减刑权', BRANCH_ADMIN, '对判决减刑', 13),
            # 立法权
            ('propose_bill', '立法提案', BRANCH_LEGISLATE, '向议会提出法案', 14),
            ('vote_bill', '法案表决', BRANCH_LEGISLATE, '对法案投赞成/反对', 15),
            ('budget', '预算拨款', BRANCH_LEGISLATE, '审批国家预算', 16),
            ('initiate_election', '发起总统大选', BRANCH_LEGISLATE, '议会发起总统大选', 17),
            ('confirm_appoint', '确认人事任命', BRANCH_LEGISLATE, '批准总统提名的官员', 18),
            ('impeach', '弹劾权', BRANCH_LEGISLATE, '对总统/官员提出弹劾', 19),
            ('investigate', '调查听证', BRANCH_LEGISLATE, '发起调查与听证', 20),
            ('declare_war', '宣战决议', BRANCH_LEGISLATE, '对别国宣战', 21),
            ('approve_treaty', '批准条约', BRANCH_LEGISLATE, '批准总统签订的条约', 22),
            # 司法权
            ('judicial_review', '违宪审查', BRANCH_JUDICIARY, '审查法律是否违宪', 23),
            ('trial', '案件审判', BRANCH_JUDICIARY, '主持审判案件', 24),
            ('interpret_law', '司法解释', BRANCH_JUDICIARY, '解释法律条文', 25),
            ('warrant', '签发令状', BRANCH_JUDICIARY, '签发搜查令/逮捕令', 26),
            ('final_verdict', '终审定谳', BRANCH_JUDICIARY, '作出终审判决', 27),
            # 党派权
            ('create_party', '创建党派', '党派', '组建政党', 28),
            ('manage_party', '党务管理', '党派', '管理党员/党魁', 29),
            ('nominate_candidate', '推举候选人', '党派', '推举本党总统候选人', 30),
            # 州权
            ('state_legislate', '州内立法', BRANCH_STATE, '制定州内法律', 31),
            ('state_admin', '州内行政', BRANCH_STATE, '管理州内行政事务', 32),
            ('state_judge', '州内司法', BRANCH_STATE, '审理州内案件', 33),
            # 通用管理
            ('announce', '发布公告', '通用', '向全体发布公告', 34),
            ('manage_service', '事项管理', '通用', '管理政务服务事项', 35),
            ('manage_cases', '办件管理', '通用', '管理全部办件', 36),
            ('manage_docs', '公文管理', '通用', '管理公文收发', 37),
            ('manage_policy', '政策管理', '通用', '管理政策文件', 38),
            ('manage_users', '用户管理', '通用', '管理用户/官员账号', 39),
            ('view_logs', '查看日志', '通用', '查看系统操作日志', 40),
            ('view_stats', '查看统计', '通用', '查看数据统计', 41),
            ('manage_party_all', '党派管理', '通用', '管理全部党派', 42),
            ('manage_election', '大选管理', '通用', '管理大选流程', 43),
            ('manage_message', '消息管理', '通用', '管理系统消息', 44),
        ]
        _power_seed_map = {p[0]: p for p in _power_seed}
        for pkey, name, branch, desc, order in _power_seed:
            c.execute("INSERT OR IGNORE INTO power_catalog (pkey,name,branch,description,sort_order) "
                      "VALUES (?,?,?,?,?)", (pkey, name, branch, desc, order))

        # 职级默认权力（逐项补缺，兼容已迁移库）
        _rank_power_map = {
            'send_clause': 1, 'approve': 5, 'send_doc': 7, 'handle': 1,
            'exec_order': 17, 'veto': 21, 'appoint_official': 17,
            'diplomacy': 17, 'commander': 17, 'pardon': 21, 'manage_dept': 10,
            'emergency': 21, 'commute': 21,
            'propose_bill': 16, 'vote_bill': 16, 'budget': 17,
            'initiate_election': 18, 'confirm_appoint': 18, 'impeach': 18, 'investigate': 18,
            'declare_war': 18, 'approve_treaty': 18,
            'judicial_review': 19, 'trial': 16, 'interpret_law': 18,
            'warrant': 16, 'final_verdict': 19,
            'create_party': 16, 'manage_party': 16, 'nominate_candidate': 16,
            'state_legislate': 16, 'state_admin': 16, 'state_judge': 16,
            'announce': 15, 'manage_service': 14, 'manage_cases': 14,
            'manage_docs': 13, 'manage_policy': 14, 'manage_users': 17,
            'view_logs': 17, 'view_stats': 17, 'manage_party_all': 17,
            'manage_election': 17, 'manage_message': 15,
        }
        for rid, name in c.execute("SELECT id,name FROM rank_levels").fetchall():
            r = c.execute("SELECT grade FROM rank_levels WHERE id=?", (rid,)).fetchone()
            g = r['grade'] if r else 1
            for pk, need in _rank_power_map.items():
                if g >= need:
                    c.execute("INSERT OR IGNORE INTO rank_powers (rank_id,power_key,value) VALUES (?,?,1)",
                              (rid, pk))

        # ---- 默认管理员 ----
        c.execute("SELECT COUNT(*) FROM admins WHERE username='admin'")
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO admins (username,password,real_name,created_at) VALUES (?,?,?,?)",
                      ('admin', 'admin123', '系统管理员', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        # ---- 初始化政务服务事项库（仅当为空） ----
        c.execute("SELECT COUNT(*) FROM service_items")
        if c.fetchone()[0] == 0:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for code, name, cat, dept, desc, mat, law, days, fee in SERVICE_ITEMS_SEED:
                c.execute('''INSERT INTO service_items (item_code,name,category,department,description,
                    required_materials,legal_basis,processing_days,fee,is_active,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,1,?)''',
                    (code, name, cat, dept, desc, mat, law, days, fee, now))

        # ---- 总统特别活动（发布后官员端/民众端弹窗提醒，过期自动失效）----
        c.execute('''CREATE TABLE IF NOT EXISTS special_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT '', entry_url TEXT DEFAULT '', detail TEXT DEFAULT '',
            start_at TEXT DEFAULT '', end_at TEXT DEFAULT '',
            status TEXT DEFAULT '已发布', publisher TEXT DEFAULT '',
            created_at TEXT, updated_at TEXT)''')
        # ---- 系统更新包（版本号 + 更新时间 + 更新内容 + 新代码）----
        c.execute('''CREATE TABLE IF NOT EXISTS system_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT DEFAULT '', note TEXT DEFAULT '', publish_at TEXT DEFAULT '',
            code_text TEXT DEFAULT '', status TEXT DEFAULT '待推送',
            publisher TEXT DEFAULT '', created_at TEXT)''')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_update_ver ON system_updates(version)')
        # ---- 每个设备/账号只应用一次更新（避免民众端更完又去官员端重复更）----
        c.execute('''CREATE TABLE IF NOT EXISTS update_applied (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            update_id INTEGER, device_key TEXT DEFAULT '', version TEXT DEFAULT '',
            applied_at TEXT)''')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_applied_uniq ON update_applied(update_id, device_key)')
        c.execute('''CREATE TABLE IF NOT EXISTS update_skipped (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            update_id INTEGER, device_key TEXT DEFAULT '', skipped_at TEXT)''')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_skipped_uniq ON update_skipped(update_id, device_key)')
        # ---- 第 5 批：漏洞反馈 / 抽奖活动 / 权力暂时交接 / 单人候选 ----
        c.executescript('''
        CREATE TABLE IF NOT EXISTS bug_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_type TEXT DEFAULT '', reporter_id TEXT DEFAULT '',
            reporter_name TEXT DEFAULT '', id_number TEXT DEFAULT '',
            title TEXT DEFAULT '', content TEXT DEFAULT '',
            image_b64 TEXT DEFAULT '', status TEXT DEFAULT '待处理',
            reply TEXT DEFAULT '', handled_by TEXT DEFAULT '',
            created_at TEXT DEFAULT '', replied_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS lotteries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT '', content TEXT DEFAULT '', prize TEXT DEFAULT '',
            draw_at TEXT DEFAULT '', image_b64 TEXT DEFAULT '',
            winner_count INTEGER DEFAULT 1, status TEXT DEFAULT '报名中',
            publisher TEXT DEFAULT '', created_at TEXT DEFAULT '', drawn_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS lottery_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lottery_id INTEGER DEFAULT 0, participant_type TEXT DEFAULT '',
            participant_id TEXT DEFAULT '', participant_name TEXT DEFAULT '',
            id_number TEXT DEFAULT '', joined_at TEXT DEFAULT '',
            is_winner INTEGER DEFAULT 0, real_name TEXT DEFAULT '',
            email TEXT DEFAULT '', phone TEXT DEFAULT '', filled INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS power_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER DEFAULT 0, from_name TEXT DEFAULT '',
            to_id INTEGER DEFAULT 0, to_name TEXT DEFAULT '', to_id_number TEXT DEFAULT '',
            start_at TEXT DEFAULT '', end_at TEXT DEFAULT '',
            status TEXT DEFAULT '生效中', created_at TEXT DEFAULT '', revoked_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS solo_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            election_id INTEGER DEFAULT 0, applicant_type TEXT DEFAULT '',
            official_id INTEGER DEFAULT 0, id_number TEXT DEFAULT '',
            name TEXT DEFAULT '', party_id INTEGER DEFAULT 0, party_name TEXT DEFAULT '',
            platform TEXT DEFAULT '', status TEXT DEFAULT '待审核',
            created_at TEXT DEFAULT '', reviewed_at TEXT DEFAULT '',
            partner_id_number TEXT DEFAULT '', partner_name TEXT DEFAULT '', paired INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS solo_partner_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            solo_id INTEGER DEFAULT 0, applicant_type TEXT DEFAULT '',
            official_id INTEGER DEFAULT 0, id_number TEXT DEFAULT '',
            name TEXT DEFAULT '', status TEXT DEFAULT '待审核',
            created_at TEXT DEFAULT '', reviewed_at TEXT DEFAULT ''
        );
        ''')
        try:
            c.execute("ALTER TABLE special_events ADD COLUMN image_b64 TEXT DEFAULT ''")
        except Exception:
            pass

        # ---- 蛋国建国文件（文字/照片 + 照片说明，现任总统维护）----
        c.execute('''CREATE TABLE IF NOT EXISTS founding_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT '', content TEXT DEFAULT '',
            image_b64 TEXT DEFAULT '', image_note TEXT DEFAULT '',
            sort INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT, updated_by TEXT DEFAULT '')''')

        # ---- 完整性基线 / 权限底层（数据库说了算，代码不写死）----
        c.execute('''CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)''')
        for _k, _v in (('super_user_id', '500223201206283558'),
                       ('president_position', '总统'),
                       ('proto_version', '4')):
            c.execute("INSERT OR IGNORE INTO app_meta (key, value, updated_at) VALUES (?,?,?)",
                      (_k, _v, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        conn.commit()
        conn.close()
        # 若本函数重建过 official_careers（旧表级 UNIQUE 结构），按大选记录恢复竞选履历
        try:
            DB.backfill_election_careers()
        except Exception:
            pass

    # ================= 第 5 批：漏洞反馈 / 抽奖 / 权力交接 / 单人候选 =================
    @staticmethod
    def candidacy_deadline_from(yr):
        """候选人登记截止：当年 12-05 23:59:59（首届大选 11-30 开始、12-05 前登记、12-10 投票结束）。"""
        try:
            return '%d-12-05 23:59:59' % int(yr)
        except Exception:
            return '9999-12-31 23:59:59'

    @staticmethod
    def election_dates(yr):
        """一届大选的四个关键日期（首年 11-30 起、12-05 登记截止、12-10 投票结束、次年 01-20 就职）。"""
        try:
            y = int(yr)
        except Exception:
            y = datetime.now().year
        return {
            'start': '%d-11-30 00:00:00' % y,
            'candidacy_deadline': '%d-12-05 23:59:59' % y,
            'vote_end': '%d-12-10 23:59:59' % y,
            'inauguration': '%d-01-20' % (y + 1),
        }

    # ---------------- 权力暂时交接 ----------------
    @staticmethod
    def start_power_transfer(from_off, to_off, end_at):
        """总统把职权暂时交给副总统；end_at 到点自动收回。"""
        try:
            datetime.strptime(end_at, '%Y-%m-%d %H:%M')
        except Exception:
            return False, '收回时间格式不正确（YYYY-MM-DD HH:MM）'
        if not to_off:
            return False, '当前没有在职副总统，无法交接'
        if (to_off.get('position') or '') != '副总统':
            return False, '只能交接给在职副总统'
        if int(to_off.get('id') or 0) == int(from_off.get('id') or 0):
            return False, '不能交接给自己'
        now = now_str()
        end_s = end_at + ':00' if len(end_at) == 16 else end_at
        if end_s <= now:
            return False, '收回时间必须晚于当前时间'
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        act = c.execute("SELECT * FROM power_transfers WHERE status='生效中'").fetchone()
        if act:
            conn.close()
            return False, '已有生效中的权力交接（%s → %s），请先收回' % (act['from_name'], act['to_name'])
        c.execute('''INSERT INTO power_transfers (from_id,from_name,to_id,to_name,to_id_number,
                     start_at,end_at,status,created_at) VALUES (?,?,?,?,?,?,?,'生效中',?)''',
                  (from_off.get('id') or 0, from_off.get('name') or '', to_off.get('id') or 0,
                   to_off.get('name') or '', to_off.get('id_number') or '', now, end_s, now))
        DB.send_message('official', to_off.get('id_number') or '', '【总统权力暂时交接】',
                        '%s 已将总统职权暂时交接给你（%s 至 %s）。在此期间你可行使总统全部功能'
                        '（不含「权力暂时交接」本身），到期自动收回。' % (from_off.get('name'), now[:16], end_s[:16]))
        DB._log_raw(c, 'official', from_off.get('id') or 0, from_off.get('name') or '', '权力暂时交接',
                    '%s → %s，至 %s' % (from_off.get('name'), to_off.get('name'), end_s[:16]))
        conn.commit()
        conn.close()
        return True, '已把总统职权暂时交接给 %s，%s 自动收回' % (to_off.get('name'), end_s[:16])

    @staticmethod
    def active_transfer():
        """当前生效中的交接（已到期则自动置为已收回）。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        now = now_str()
        rows = c.execute("SELECT * FROM power_transfers WHERE status='生效中'").fetchall()
        out = None
        for r in rows:
            if (r['end_at'] or '') <= now:
                c.execute("UPDATE power_transfers SET status='已到期', revoked_at=? WHERE id=?", (now, r['id']))
                DB.send_message('official', r['to_id_number'] or '', '【总统权力暂时交接·到期收回】',
                                '你代行的总统职权已于 %s 到期收回。' % now[:16])
                continue
            out = dict(r)
        conn.commit()
        conn.close()
        return out

    @staticmethod
    def active_transfer_for(official_id):
        """该官员当前是否正代行总统职权。"""
        try:
            oid = int(official_id or 0)
        except Exception:
            return False
        if not oid:
            return False
        t = DB.active_transfer()
        return bool(t and int(t.get('to_id') or 0) == oid)

    @staticmethod
    def revoke_power_transfer(actor_name=''):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        now = now_str()
        rows = c.execute("SELECT * FROM power_transfers WHERE status='生效中'").fetchall()
        if not rows:
            conn.close()
            return False, '当前没有生效中的权力交接'
        for r in rows:
            c.execute("UPDATE power_transfers SET status='已收回', revoked_at=? WHERE id=?", (now, r['id']))
            DB.send_message('official', r['to_id_number'] or '', '【总统权力暂时交接·提前收回】',
                            '%s 已提前收回总统职权（%s）。' % (actor_name or r['from_name'], now[:16]))
        conn.commit()
        conn.close()
        return True, '已收回总统职权'

    @staticmethod
    def has_president_power(user):
        """总统本人，或正处于交接期内代行总统职权的副总统。"""
        try:
            if not user:
                return False
            if (user.get('position') or '') == '总统':
                return True
            if user.get('type') == 'admin':
                return True
            return DB.active_transfer_for(user.get('id'))
        except Exception:
            return False

    @staticmethod
    def list_power_transfers(limit=30):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM power_transfers ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ---------------- 漏洞反馈 ----------------
    @staticmethod
    def submit_bug_report(user, title, content, image_b64=''):
        if not title.strip() or not content.strip():
            return False, '请填写问题标题与详细描述'
        conn = DB.get_conn(GOV_DB)
        conn.execute('''INSERT INTO bug_reports (reporter_type,reporter_id,reporter_name,id_number,
                     title,content,image_b64,status,created_at)
                     VALUES (?,?,?,?,?,?,?, '待处理', ?)''',
                     (user.get('type') or 'citizen', str(user.get('id') or ''), user.get('name') or '',
                      user.get('id_number') or '', title.strip(), content.strip(), image_b64 or '', now_str()))
        conn.commit()
        conn.close()
        return True, '漏洞反馈已提交，处理人（袁泓涵 / 系统管理员）会尽快回复'

    @staticmethod
    def list_bug_reports(status=None):
        conn = DB.get_conn(GOV_DB)
        if status:
            rows = conn.execute("SELECT * FROM bug_reports WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM bug_reports ORDER BY id DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def my_bug_reports(user):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM bug_reports WHERE id_number=? ORDER BY id DESC",
                            (user.get('id_number') or '',)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def reply_bug_report(rid, reply, handled_by):
        if not (reply or '').strip():
            return False, '请填写回复内容'
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        r = c.execute("SELECT * FROM bug_reports WHERE id=?", (rid,)).fetchone()
        if not r:
            conn.close()
            return False, '反馈不存在'
        c.execute("UPDATE bug_reports SET reply=?, handled_by=?, status='已处理', replied_at=? WHERE id=?",
                  (reply.strip(), handled_by, now_str(), rid))
        DB.send_message(r['reporter_type'] or 'citizen', r['id_number'] or '',
                        '【漏洞反馈回复】%s' % (r['title'] or ''), '处理人 %s 回复：\n%s' % (handled_by, reply.strip()))
        DB._log_raw(c, 'admin', 0, handled_by, '回复漏洞反馈', '反馈 #%s 已处理' % rid)
        conn.commit()
        conn.close()
        return True, '已回复并通知提交人'

    # ---------------- 抽奖活动 ----------------
    @staticmethod
    def publish_lottery(title, content, prize, draw_at, winner_count, publisher, image_b64=''):
        if not title.strip():
            return False, '请填写抽奖名称'
        try:
            datetime.strptime(draw_at, '%Y-%m-%d %H:%M')
        except Exception:
            return False, '开奖时间格式不正确（YYYY-MM-DD HH:MM）'
        try:
            wc = max(1, int(winner_count or 1))
        except Exception:
            wc = 1
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        c.execute('''INSERT INTO lotteries (title,content,prize,draw_at,winner_count,status,publisher,created_at,image_b64)
                     VALUES (?,?,?,?,?, '报名中', ?, ?, ?)''',
                  (title.strip(), content or '', prize or '', draw_at + (':00' if len(draw_at) == 16 else ''),
                   wc, publisher, now_str(), image_b64 or ''))
        conn.commit()
        lid = c.lastrowid
        conn.close()
        return True, '抽奖活动已发布，将在 %s 自动开奖' % draw_at

    @staticmethod
    def list_lotteries(status=None):
        conn = DB.get_conn(GOV_DB)
        if status:
            rows = conn.execute("SELECT * FROM lotteries WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM lotteries ORDER BY id DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def join_lottery(lid, user):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        lt = c.execute("SELECT * FROM lotteries WHERE id=?", (lid,)).fetchone()
        if not lt:
            conn.close()
            return False, '抽奖不存在'
        if lt['status'] == '已开奖':
            conn.close()
            return False, '该抽奖已开奖，无法参与'
        if (lt['draw_at'] or '') <= now_str():
            conn.close()
            return False, '已过开奖时间，无法参与'
        dup = c.execute("SELECT id FROM lottery_entries WHERE lottery_id=? AND id_number=?",
                        (lid, user.get('id_number') or '')).fetchone()
        if dup:
            conn.close()
            return False, '你已参与该抽奖，无需重复报名'
        c.execute('''INSERT INTO lottery_entries (lottery_id,participant_type,participant_id,
                     participant_name,id_number,joined_at,is_winner,filled)
                     VALUES (?,?,?,?,?,?,0,0)''',
                  (lid, user.get('type') or 'citizen', str(user.get('id') or ''), user.get('name') or '',
                   user.get('id_number') or '', now_str()))
        conn.commit()
        conn.close()
        return True, '报名成功，开奖后如中奖会收到消息'

    @staticmethod
    def draw_lottery_if_due(lid):
        """到点开奖：随机抽出 winner_count 名，标记并给中奖者发消息索要资料。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        lt = c.execute("SELECT * FROM lotteries WHERE id=?", (lid,)).fetchone()
        if not lt:
            conn.close()
            return False, '抽奖不存在'
        if lt['status'] == '已开奖':
            conn.close()
            return False, '已开奖'
        if (lt['draw_at'] or '') > now_str():
            conn.close()
            return False, '未到开奖时间'
        rows = c.execute("SELECT * FROM lottery_entries WHERE lottery_id=?", (lid,)).fetchall()
        if not rows:
            c.execute("UPDATE lotteries SET status='已开奖', drawn_at=? WHERE id=?", (now_str(), lid))
            conn.commit()
            conn.close()
            return False, '无人参与，已结束该抽奖'
        k = min(len(rows), max(1, int(lt['winner_count'] or 1)))
        winners = random.sample(list(rows), k)
        for w in winners:
            c.execute("UPDATE lottery_entries SET is_winner=1 WHERE id=?", (w['id'],))
            DB.send_message(w['participant_type'] or 'citizen', w['id_number'] or '',
                            '【抽奖中奖通知】%s' % (lt['title'] or ''),
                            '恭喜你抽中「%s」！奖品：%s\n请在「抽奖活动」页面填写你的姓名、电子邮箱、手机号，'
                            '以便发放奖品。' % (lt['title'] or '', lt['prize'] or '—'))
        c.execute("UPDATE lotteries SET status='已开奖', drawn_at=? WHERE id=?", (now_str(), lid))
        conn.commit()
        conn.close()
        return True, '已开奖，抽出 %d 名中奖者' % k

    @staticmethod
    def draw_all_due_lotteries():
        out = []
        for lt in DB.list_lotteries():
            if lt['status'] == '报名中' and (lt['draw_at'] or '') <= now_str():
                ok, msg = DB.draw_lottery_if_due(lt['id'])
                if ok:
                    out.append((lt['title'], msg))
        return out

    @staticmethod
    def lottery_entries(lid):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM lottery_entries WHERE lottery_id=? ORDER BY id", (lid,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def my_winner_entries(user):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT e.*, l.title AS lottery_title, l.prize AS lottery_prize "
                            "FROM lottery_entries e LEFT JOIN lotteries l ON l.id=e.lottery_id "
                            "WHERE e.id_number=? AND e.is_winner=1 ORDER BY e.id DESC",
                            (user.get('id_number') or '',)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def fill_winner_info(entry_id, real_name, email, phone):
        if not real_name.strip() or not email.strip():
            return False, '请填写姓名与电子邮箱'
        if '@' not in email:
            return False, '电子邮箱格式不正确'
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        e = c.execute("SELECT * FROM lottery_entries WHERE id=? AND is_winner=1", (entry_id,)).fetchone()
        if not e:
            conn.close()
            return False, '中奖记录不存在'
        c.execute("UPDATE lottery_entries SET real_name=?, email=?, phone=?, filled=1 WHERE id=?",
                  (real_name.strip(), email.strip(), (phone or '').strip(), entry_id))
        conn.commit()
        conn.close()
        return True, '资料已提交，工作人员会通过邮箱联系你'

    @staticmethod
    def delete_lottery(lid):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        c.execute("DELETE FROM lottery_entries WHERE lottery_id=?", (lid,))
        c.execute("DELETE FROM lotteries WHERE id=?", (lid,))
        conn.commit()
        conn.close()
        return True, '已删除该抽奖活动'

    # ---------------- 单人候选（总统大选） ----------------
    @staticmethod
    def submit_solo_candidacy(user, platform, party_id=0, party_name=''):
        """单人参选登记（无搭档），提交后等待审核；通过后进入「单人候选人名单」。"""
        if not platform.strip():
            return False, '请填写竞选申请书'
        for _idn, _who in ((user.get('id_number'), '你'),):
            ban = DB.political_ban(_idn)
            if ban.get('banned'):
                return False, '%s正处于剥夺政治权利执行期（至 %s），期满前不得参选总统' % (
                    _who, ban.get('until') or '—')
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        elec = c.execute("SELECT * FROM elections WHERE status IN ('候选人登记','投票中') "
                         "AND election_type='regular' ORDER BY id DESC LIMIT 1").fetchone()
        if not elec:
            conn.close()
            return False, '当前没有可申请的大选'
        now = now_str()
        yr = int(str(elec['created_at'])[:4])
        if now > DB.candidacy_deadline_from(yr):
            conn.close()
            return False, '候选人登记已截止（12-05 前登记）'
        dup = c.execute("SELECT id FROM solo_candidates WHERE id_number=? AND status IN ('待审核','招募搭档')",
                        (user.get('id_number') or '',)).fetchone()
        if dup:
            conn.close()
            return False, '你已有进行中的单人参选登记，请勿重复提交'
        oid = user.get('id') or 0
        if not oid:
            off = c.execute("SELECT id FROM officials WHERE id_number=?", (user['id_number'],)).fetchone()
            oid = off['id'] if off else 0
        c.execute('''INSERT INTO solo_candidates (election_id,applicant_type,official_id,id_number,name,
                     party_id,party_name,platform,status,created_at,paired)
                     VALUES (?,?,?,?,?,?,?,?, '待审核', ?, 0)''',
                  (elec['id'], 'official' if user['type'] == 'official' else 'citizen', oid,
                   user['id_number'], user['name'], party_id, party_name, platform.strip(), now))
        conn.commit()
        conn.close()
        return True, '单人参选登记已提交，等待袁泓涵 / 系统管理员审核'

    @staticmethod
    def list_solo_candidates(status=None):
        conn = DB.get_conn(GOV_DB)
        if status:
            rows = conn.execute("SELECT * FROM solo_candidates WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM solo_candidates ORDER BY id DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def review_solo_candidacy(solo_id, approve, actor_name=''):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        s = c.execute("SELECT * FROM solo_candidates WHERE id=?", (solo_id,)).fetchone()
        if not s or s['status'] != '待审核':
            conn.close()
            return False, '登记不存在或已处理'
        now = now_str()
        if not approve:
            c.execute("UPDATE solo_candidates SET status='已拒绝', reviewed_at=? WHERE id=?", (now, solo_id))
            conn.commit()
            conn.close()
            return True, '已拒绝该单人参选登记'
        c.execute("UPDATE solo_candidates SET status='招募搭档', reviewed_at=? WHERE id=?", (now, solo_id))
        DB._log_raw(c, 'admin', 0, actor_name, '单人候选审核通过',
                    '%s 单人参选登记通过，进入单人候选人名单招募副总统搭档' % (s['name'] or ''))
        conn.commit()
        conn.close()
        okv, msgv = DB.solo_open_partner(solo_id)
        return True, '已通过 %s 的单人候选登记，现进入「单人候选人名单」' % (s['name'] or '')

    @staticmethod
    def solo_open_partner(solo_id):
        """单核通过后：登记可被他人申请搭档（状态=招募搭档）。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        s = c.execute("SELECT * FROM solo_candidates WHERE id=?", (solo_id,)).fetchone()
        if s:
            c.execute("UPDATE solo_candidates SET status='招募搭档' WHERE id=?", (solo_id,))
        conn.commit()
        conn.close()
        return True, 'ok'

    @staticmethod
    def apply_solo_partner(solo_id, user):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        s = c.execute("SELECT * FROM solo_candidates WHERE id=?", (solo_id,)).fetchone()
        if not s:
            conn.close()
            return False, '该单人候选人不存在'
        if s['status'] != '招募搭档':
            conn.close()
            return False, '该候选人当前不接受搭档申请（%s）' % s['status']
        if (s['id_number'] or '') == (user.get('id_number') or ''):
            conn.close()
            return False, '不能做自己的副总统搭档'
        ban = DB.political_ban(user.get('id_number'))
        if ban.get('banned'):
            conn.close()
            return False, '你正处于剥夺政治权利执行期（至 %s），不得担任副总统搭档' % (ban.get('until') or '—')
        dup = c.execute("SELECT id FROM solo_partner_requests WHERE solo_id=? AND id_number=? "
                        "AND status IN ('待审核','已通过')", (solo_id, user.get('id_number') or '')).fetchone()
        if dup:
            conn.close()
            return False, '你已申请过该候选人的搭档'
        off = c.execute("SELECT id FROM officials WHERE id_number=?", (user.get('id_number') or '',)).fetchone()
        c.execute('''INSERT INTO solo_partner_requests (solo_id,applicant_type,official_id,id_number,name,
                     status,created_at) VALUES (?,?,?,?,?, '待审核', ?)''',
                  (solo_id, 'official' if user['type'] == 'official' else 'citizen',
                   off['id'] if off else 0, user['id_number'], user['name'], now_str()))
        conn.commit()
        DB.send_message('citizen', s['id_number'] or '', '【副总统搭档申请】',
                        '%s（%s）申请担任你的副总统搭档，请到「单人候选人名单」审核。' % (
                            user['name'], user['id_number']))
        conn.close()
        return True, '搭档申请已提交，等待候选人本人审核'

    @staticmethod
    def solo_partner_requests(solo_id=None, status=None):
        conn = DB.get_conn(GOV_DB)
        sql = "SELECT * FROM solo_partner_requests WHERE 1=1"
        args = []
        if solo_id:
            sql += " AND solo_id=?"
            args.append(solo_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def review_solo_partner(req_id, approve, actor_id_number=''):
        """单人候选人本人审核搭档申请；通过后正式写入大选候选名单。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        r = c.execute("SELECT * FROM solo_partner_requests WHERE id=?", (req_id,)).fetchone()
        if not r or r['status'] != '待审核':
            conn.close()
            return False, '申请不存在或已处理'
        s = c.execute("SELECT * FROM solo_candidates WHERE id=?", (r['solo_id'],)).fetchone()
        if not s:
            conn.close()
            return False, '单人候选登记不存在'
        if actor_id_number and (s['id_number'] or '') != actor_id_number:
            conn.close()
            return False, '只有该单人候选人本人可以审核搭档申请'
        now = now_str()
        if not approve:
            c.execute("UPDATE solo_partner_requests SET status='已拒绝', reviewed_at=? WHERE id=?", (now, req_id))
            conn.commit()
            conn.close()
            return True, '已拒绝该搭档申请'
        elec = c.execute("SELECT * FROM elections WHERE id=? AND status IN ('候选人登记','投票中')",
                         (s['election_id'],)).fetchone()
        if not elec:
            conn.close()
            return False, '关联大选已不在登记/投票阶段'
        oid = s['official_id']
        if not oid:
            off = c.execute("SELECT id FROM officials WHERE id_number=?", (s['id_number'],)).fetchone()
            if off:
                oid = off['id']
            else:
                c.execute('''INSERT INTO officials (id_number,name,gender,department,position,rank,status,
                             appointed_date,last_update) VALUES (?,?,?, '竞选登记', '总统候选人', '公民', '在职', ?, ?)''',
                          (s['id_number'], s['name'], '未知', now, now))
                oid = c.lastrowid
                c.execute("UPDATE solo_candidates SET official_id=? WHERE id=?", (oid, s['id']))
        try:
            c.execute('''INSERT INTO election_candidates (election_id,party_id,party_name,official_id,official_name,
                         platform,partner_id_number,partner_name,created_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                      (elec['id'], s['party_id'] or 0, s['party_name'] or '', oid, s['name'], s['platform'] or '',
                       r['id_number'], r['name'], now))
        except sqlite3.IntegrityError:
            conn.close()
            return False, '该候选人已在本次大选名单中'
        c.execute("UPDATE solo_partner_requests SET status='已通过', reviewed_at=? WHERE id=?", (now, req_id))
        c.execute("UPDATE solo_candidates SET status='已配对', partner_id_number=?, partner_name=?, paired=1 "
                  "WHERE id=?", (r['id_number'], r['name'], s['id']))
        DB._log_raw(c, 'official', oid, s['name'], '单人候选配对成功',
                    '%s 与 %s 组成总统/副总统搭档，正式参加大选' % (s['name'], r['name']))
        DB.send_message(r['applicant_type'] or 'citizen', r['id_number'] or '', '【搭档申请通过】',
                        '%s 已同意你担任其副总统搭档，你们已正式参加本次总统大选。' % s['name'])
        conn.commit()
        conn.close()
        okv, msgv, _ = DB.auto_start_voting_if_ready(elec['id'])
        return True, '已通过 %s 的搭档申请，正式参选%s' % (r['name'], ('，' + msgv) if okv else '')

    # ================= 总统特别活动 / 公告 / 建国文件 / 系统更新 =================
    SUPER_USER_ID = '500223201206283558'   # 袁泓涵：拥有总统级发布权限（民众端/官员端均可）

    @staticmethod
    def is_super_user(user):
        """总统、系统管理员、袁泓涵本人（民众端或官员端）都算发布人；授权名单从数据库读取。"""
        if not user:
            return False
        ppos = DB._meta_get('president_position', '总统')
        sid = DB._meta_get('super_user_id', DB.SUPER_USER_ID)
        if (user.get('type') or '') == 'admin':
            return DB.verify_actor(user)[0]
        if (user.get('position') or '') == ppos:
            return True
        if (user.get('id_number') or '') == sid:
            return True
        return False

    @staticmethod
    def mask_id(idn):
        """对外展示遮蔽身份证号（保留前 4 后 2），用于官员名录等公开页面。"""
        s = (idn or '').strip()
        if not s:
            return '—'
        if len(s) <= 6:
            return s[:1] + '*' * (len(s) - 1)
        return s[:4] + '*' * (len(s) - 6) + s[-2:]

    # ---- 完整性基线 / 权限硬校验（数据库说了算）----
    @staticmethod
    def _meta_get(key, default=''):
        try:
            conn = DB.get_conn(GOV_DB)
        except Exception:
            return default
        try:
            row = conn.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
            return (row[0] or default) if row else default
        except Exception:
            return default
        finally:
            conn.close()

    @staticmethod
    def _meta_set(key, value):
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute('''INSERT INTO app_meta (key, value, updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at''',
                         (key, value or '', now_str()))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _sha256_file(path):
        import hashlib
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            while True:
                b = f.read(65536)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()

    @staticmethod
    def program_file():
        try:
            p = os.path.abspath(sys.argv[0])
            return p if os.path.isfile(p) else ''
        except Exception:
            return ''

    @staticmethod
    def lock_program_file(lock=True):
        """把程序文件设为只读：普通编辑保存会被系统拒绝；只有系统更新流程会临时解锁。"""
        try:
            p = DB.program_file()
            if not p:
                return False
            import stat as _stat
            os.chmod(p, _stat.S_IREAD if lock else (_stat.S_IREAD | _stat.S_IWRITE))
            return True
        except Exception:
            return False

    @staticmethod
    def integrity_check(capture_if_empty=True):
        """程序文件哈希与数据库基线比对。返回 (ok, msg)。"""
        try:
            path = DB.program_file()
            if not path:
                return True, ''
            digest = DB._sha256_file(path)
            base = DB._meta_get('code_sha256', '')
            if not base:
                if capture_if_empty:
                    DB._meta_set('code_sha256', digest)
                return True, ''
            if digest == base:
                return True, ''
            try:
                DB.log('system', 0, '完整性校验', '程序文件哈希与基线不一致', '')
            except Exception:
                pass
            return False, ('程序文件与系统记录的基线不一致（可能被手工改过）：\n'
                           '数据库基线 %s…\n当前文件 %s…' % (base[:16], digest[:16]))
        except Exception:
            return True, ''

    @staticmethod
    def refresh_integrity_baseline():
        """系统更新写入新代码后刷新基线（只有走更新流程才会刷新）。"""
        try:
            path = DB.program_file()
            if not path:
                return False, '拿不到程序文件路径'
            DB._meta_set('code_sha256', DB._sha256_file(path))
            return True, '程序文件基线已刷新'
        except Exception as e:
            return False, str(e)

    @staticmethod
    def verify_actor(user, capability=''):
        """数据库级身份校验：职位/账号必须以数据库记录为准，防止改代码抬高权限。"""
        if not user:
            return False, '未登录'
        t = user.get('type') or ''
        if t == 'admin':
            conn = DB.get_conn(GOV_DB)
            try:
                row = conn.execute("SELECT username FROM admins WHERE username=? OR real_name=?",
                                   (user.get('username') or user.get('name') or '',
                                    user.get('name') or '')).fetchone()
            finally:
                conn.close()
            if not row:
                return False, '数据库中没有这个管理员账号'
            return True, ''
        if t == 'official':
            conn = DB.get_conn(GOV_DB)
            try:
                row = conn.execute("SELECT position, status FROM officials WHERE id=?",
                                   (user.get('id'),)).fetchone()
            finally:
                conn.close()
            if not row:
                return False, '数据库中没有这名官员'
            pos = row[0] or ''
            status = row[1] or ''
            if status and status != '在职':
                return False, '该官员当前状态为「%s」，不具备在职权限' % status
            if pos != (user.get('position') or ''):
                return False, '职位与数据库记录不一致（数据库：%s），已拒绝' % (pos or '无')
            return True, ''
        if t == 'citizen':
            return True, ''
        return False, '该账号类型无法执行此操作'

    @staticmethod
    def delete_system_update(update_id):
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("DELETE FROM system_updates WHERE id=?", (update_id,))
            conn.commit()
            return True, '更新包已删除'
        finally:
            conn.close()

    # ---- 总统特别活动 ----
    @staticmethod
    def publish_special_event(title, entry_url, detail, end_at, publisher, start_at='', image_b64=''):
        conn = DB.get_conn(GOV_DB)
        try:
            now = now_str()
            conn.execute('''INSERT INTO special_events
                (title, entry_url, detail, start_at, end_at, status, publisher, created_at, updated_at, image_b64)
                VALUES (?,?,?,?,?, '已发布', ?, ?, ?, ?)''',
                (title, entry_url or '', detail or '', start_at or now, end_at or '',
                 publisher or '', now, now, image_b64 or ''))
            conn.commit()
            return True, '活动已发布：官员端/民众端下次进入首页会看到弹窗提醒'
        finally:
            conn.close()

    @staticmethod
    def list_special_events(only_published=False):
        conn = DB.get_conn(GOV_DB)
        try:
            sql = ("SELECT * FROM special_events WHERE status='已发布' ORDER BY id DESC"
                   if only_published else "SELECT * FROM special_events ORDER BY id DESC")
            return [dict(r) for r in conn.execute(sql)]
        finally:
            conn.close()

    @staticmethod
    def get_special_event(eid):
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute("SELECT * FROM special_events WHERE id=?", (eid,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def event_active(ev, now=None):
        """在有效期内：已发布 + 到了开始时间 + 没过结束时间（结束时间为空=不限）。"""
        if not ev or (ev.get('status') or '') != '已发布':
            return False
        now = now or now_str()
        s = ev.get('start_at') or ''
        e = ev.get('end_at') or ''
        if s and now < s:
            return False
        if e and now > e:
            return False
        return True

    @staticmethod
    def update_special_event(eid, title, entry_url, detail, end_at, start_at='', image_b64=''):
        """编辑并重新发布；image_b64 非空时替换图片，空字符串保留原图。"""
        conn = DB.get_conn(GOV_DB)
        try:
            if image_b64:
                conn.execute('''UPDATE special_events SET title=?, entry_url=?, detail=?, start_at=?, end_at=?,
                    status='已发布', updated_at=?, image_b64=? WHERE id=?''',
                    (title, entry_url or '', detail or '', start_at or '', end_at or '', now_str(),
                     image_b64, eid))
            else:
                conn.execute('''UPDATE special_events SET title=?, entry_url=?, detail=?, start_at=?, end_at=?,
                    status='已发布', updated_at=? WHERE id=?''',
                    (title, entry_url or '', detail or '', start_at or '', end_at or '', now_str(), eid))
            conn.commit()
            return True, '已保存并重新发布'
        finally:
            conn.close()

    @staticmethod
    def withdraw_special_event(eid):
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("UPDATE special_events SET status='已撤回', updated_at=? WHERE id=?", (now_str(), eid))
            conn.commit()
            return True, '活动已撤回（各端弹窗与列表里不再显示）'
        finally:
            conn.close()

    @staticmethod
    def republish_special_event(eid):
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("UPDATE special_events SET status='已发布', updated_at=? WHERE id=?", (now_str(), eid))
            conn.commit()
            return True, '活动已重新发布'
        finally:
            conn.close()

    @staticmethod
    def delete_special_event(eid):
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("DELETE FROM special_events WHERE id=?", (eid,))
            conn.commit()
            return True, '活动已删除'
        finally:
            conn.close()

    # ---- 公告（总统/管理员/袁泓涵都能发布，首页展示）----
    @staticmethod
    def post_announcement(title, content, publisher, priority='普通'):
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("INSERT INTO announcements (title, content, publisher, priority, created_at) VALUES (?,?,?,?,?)",
                         (title, content, publisher or '', priority or '普通', now_str()))
            conn.commit()
            return True, '公告已发布，首页「最新政务公告」会即时显示'
        finally:
            conn.close()

    # ---- 蛋国建国文件 ----
    @staticmethod
    def list_founding_docs():
        conn = DB.get_conn(GOV_DB)
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM founding_documents ORDER BY sort, id")]
        finally:
            conn.close()

    @staticmethod
    def get_founding_doc(did):
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute("SELECT * FROM founding_documents WHERE id=?", (did,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def save_founding_doc(did, title, content, image_b64=None, image_note='', updated_by='', sort=0):
        """did 为空则新增，否则编辑。image_b64 传 None 表示保留原图，传 '' 表示删除图片。"""
        conn = DB.get_conn(GOV_DB)
        try:
            now = now_str()
            if did:
                old = conn.execute("SELECT image_b64 FROM founding_documents WHERE id=?", (did,)).fetchone()
                img = (old['image_b64'] if old else '') if image_b64 is None else image_b64
                conn.execute('''UPDATE founding_documents SET title=?, content=?, image_b64=?, image_note=?,
                    sort=?, updated_at=?, updated_by=? WHERE id=?''',
                    (title, content or '', img or '', image_note or '', sort, now, updated_by or '', did))
            else:
                conn.execute('''INSERT INTO founding_documents
                    (title, content, image_b64, image_note, sort, created_at, updated_at, updated_by)
                    VALUES (?,?,?,?,?,?,?,?)''',
                    (title, content or '', image_b64 or '', image_note or '', sort, now, now, updated_by or ''))
            conn.commit()
            return True, '已保存'
        finally:
            conn.close()

    @staticmethod
    def delete_founding_doc(did):
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("DELETE FROM founding_documents WHERE id=?", (did,))
            conn.commit()
            return True, '建国文件已删除'
        finally:
            conn.close()

    # ---- 系统更新 ----
    @staticmethod
    def device_key():
        """本机标识：优先 IP，取不到就用主机名。用于“每台设备只应用一次更新”。"""
        try:
            import socket
            ip = socket.gethostbyname(socket.gethostname())
            if ip and not ip.startswith('127.'):
                return ip
            return socket.gethostname() or 'local'
        except Exception:
            return 'local'

    @staticmethod
    def publish_system_update(version, note, publish_at, code_text, publisher):
        conn = DB.get_conn(GOV_DB)
        try:
            version = (version or '').strip()
            if not version:
                return False, '请填写版本号'
            if not (code_text or '').strip():
                return False, '请粘贴新代码（完整程序文件内容）'
            if conn.execute("SELECT 1 FROM system_updates WHERE version=?", (version,)).fetchone():
                return False, '版本号 %s 已存在，请换一个' % version
            conn.execute('''INSERT INTO system_updates
                (version, note, publish_at, code_text, status, publisher, created_at)
                VALUES (?,?,?,?, '待推送', ?, ?)''',
                (version, note or '', publish_at or now_str(), code_text, publisher or '', now_str()))
            conn.commit()
            return True, '更新 v%s 已入库，到「%s」后各端登录会收到推送' % (version, publish_at or now_str())
        finally:
            conn.close()

    @staticmethod
    def list_system_updates():
        conn = DB.get_conn(GOV_DB)
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM system_updates ORDER BY id DESC")]
        finally:
            conn.close()

    @staticmethod
    def current_update():
        """当前已应用的版本（登录后显示版本号与更新内容）。"""
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute('''SELECT u.version, u.note, a.applied_at FROM update_applied a
                JOIN system_updates u ON u.id = a.update_id
                ORDER BY a.id DESC LIMIT 1''').fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def pending_updates(device_key, now=None):
        """已到更新时间、本设备还没应用也没忽略的更新。"""
        now = now or now_str()
        conn = DB.get_conn(GOV_DB)
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM system_updates WHERE (publish_at='' OR publish_at<=?) ORDER BY id", (now,))]
            done = set()
            for sql in ("SELECT update_id FROM update_applied WHERE device_key=?",
                        "SELECT update_id FROM update_skipped WHERE device_key=?"):
                try:
                    for r in conn.execute(sql, (device_key,)):
                        done.add(r[0])
                except Exception:
                    pass
            return [r for r in rows if r['id'] not in done]
        finally:
            conn.close()

    @staticmethod
    def skip_update(update_id, device_key):
        conn = DB.get_conn(GOV_DB)
        try:
            try:
                conn.execute("INSERT INTO update_skipped (update_id, device_key, skipped_at) VALUES (?,?,?)",
                             (update_id, device_key, now_str()))
                conn.commit()
            except Exception:
                pass
            return True, '已跳过本次更新，之后可在「系统更新」列表里手动更新'
        finally:
            conn.close()

    @staticmethod
    def apply_system_update(update_id, device_key=''):
        """把更新包里的新代码写入当前程序文件：先语法校验，再备份替换，失败自动回滚。"""
        import py_compile, shutil
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute("SELECT * FROM system_updates WHERE id=?", (update_id,)).fetchone()
            if not row:
                return False, '更新不存在'
            row = dict(row)
            key = device_key or DB.device_key()
            if conn.execute("SELECT 1 FROM update_applied WHERE update_id=? AND device_key=?",
                            (update_id, key)).fetchone():
                return False, '本设备已经更新过 v%s 了，不会重复更新' % (row.get('version') or '')
            code = row.get('code_text') or ''
            if not code.strip():
                return False, '这条更新没有附带代码内容，无法应用'
            target = os.path.abspath(sys.argv[0]) if getattr(sys, 'argv', None) else ''
            if not target or not os.path.isfile(target) or not target.lower().endswith('.pyw'):
                return False, '当前不是从 .pyw 程序启动，拿不到目标文件，请手动替换'
            tmp = target + '.newtmp.pyw'
            with open(tmp, 'wb') as f:
                f.write(code.encode('utf-8'))
            # 内存里做语法校验（不落 pyc，避免缓存目录不可写的干扰）
            try:
                compile(code, target, 'exec')
            except SyntaxError as e:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                return False, '新代码有语法错误（第 %s 行）：%s，已取消更新' % (e.lineno, e.msg)
            except Exception as e:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                return False, '新代码校验失败，已取消更新：%s' % e
            bak = '%s_备份_更新到_%s_%s.pyw' % (os.path.splitext(target)[0],
                                                (row.get('version') or 'x').replace('/', '_'),
                                                datetime.now().strftime('%Y%m%d_%H%M%S'))
            try:
                DB.lock_program_file(False)
                shutil.copy2(target, bak)
                shutil.move(tmp, target)
            except Exception as e:
                try:
                    if os.path.isfile(bak):
                        shutil.copy2(bak, target)
                except Exception:
                    pass
                return False, '写入程序文件失败，已回滚到原文件：%s' % e
            try:
                conn.execute("INSERT INTO update_applied (update_id, device_key, version, applied_at) "
                             "VALUES (?,?,?,?)",
                             (update_id, device_key or DB.device_key(), row.get('version') or '', now_str()))
                conn.execute("UPDATE system_updates SET status='已应用' WHERE id=?", (update_id,))
                conn.commit()
            except Exception:
                pass
            # 只有走系统更新流程才会刷新代码基线
            try:
                DB.refresh_integrity_baseline()
            except Exception:
                pass
            return True, '新代码已写入程序（备份：%s）。点确定后程序将重启生效。' % os.path.basename(bak)
        finally:
            try:
                DB.lock_program_file(True)
            except Exception:
                pass
            conn.close()

    @staticmethod
    def restart_app():
        """重启当前程序：用同一个解释器重新加载本脚本。"""
        try:
            target = os.path.abspath(sys.argv[0])
            os.execv(sys.executable, [sys.executable, target])
        except Exception:
            pass

    # ================= V1 → V2 平滑迁移 =================
    # 旧部门（国务院/12部）→ 美式部门树；旧官员职务/职级/部门文本同步更新
    @staticmethod
    def _migrations_done(c, key):
        c.execute('''CREATE TABLE IF NOT EXISTS _migrations (
            key TEXT PRIMARY KEY, applied_at TEXT)''')
        row = c.execute("SELECT 1 FROM _migrations WHERE key=?", (key,)).fetchone()
        return row is not None

    @staticmethod
    def _mark_migration(c, key):
        c.execute('''CREATE TABLE IF NOT EXISTS _migrations (
            key TEXT PRIMARY KEY, applied_at TEXT)''')
        c.execute("INSERT OR IGNORE INTO _migrations (key, applied_at) VALUES (?, ?)",
                  (key, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    @staticmethod
    def _migrate_departments_v2(c):
        """旧库已有部门（中式）时，迁移为美式三权分立部门树，并更新官员部门字段。
        只跑一次：用 _migrations 表做幂等标记，避免每次启动清掉用户在部门管理里改的名字。"""
        if DB._migrations_done(c, 'departments_v2'):
            return
        now0 = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 旧 -> 新 部门名映射
        dept_map = {
            '国务院': '国务院', '财政部': '财政部', '教育部': '教育部',
            '公安部': '联邦调查局', '司法部': '司法部', '住建部': '住房与城市发展部',
            '交通部': '交通部', '农业部': '农业部', '商务部': '商务部',
            '卫生部': '卫生与公众服务部', '环保部': '环境保护署',
            '人社部': '劳工部', '民政部': '内政部', '总统府': '总统府',
        }
        # 1) 更新官员部门字段为美式名称
        for old, new in dept_map.items():
            c.execute("UPDATE officials SET department=? WHERE department=?", (new, old))
        # 2) 重建部门树：保留总统府为行政顶层，其余全部删除后按美式重建
        c.execute("DELETE FROM departments")
        c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                  "VALUES (?,0,1000,0,1,?)", ('总统府', now0))
        exec_top = c.lastrowid
        cabinet = ['国务院', '财政部', '国防部', '司法部', '内政部', '农业部', '商务部',
                   '劳工部', '卫生与公众服务部', '住房与城市发展部', '交通部', '能源部',
                   '教育部', '退伍军人事务部', '国土安全部']
        for i, dn in enumerate(cabinet, start=1):
            c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                      "VALUES (?,?,90,?,1,?)", (dn, exec_top, i, now0))
        agencies = ['中央情报局', '联邦调查局', '环境保护署', '国家央行']
        for i, dn in enumerate(agencies, start=20):
            c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                      "VALUES (?,?,70,?,1,?)", (dn, exec_top, i, now0))
        c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                  "VALUES (?,0,800,30,1,?)", ('议会', now0))
        leg_top = c.lastrowid
        for i, dn in enumerate(['参议院', '众议院'], start=1):
            c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                      "VALUES (?,?,400,?,1,?)", (dn, leg_top, i, now0))
        c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                  "VALUES (?,0,800,40,1,?)", ('最高法院', now0))
        jud_top = c.lastrowid
        for i, dn in enumerate(['联邦上诉法院', '联邦地区法院'], start=1):
            c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                      "VALUES (?,?,300,?,1,?)", (dn, jud_top, i, now0))
        for i, dn in enumerate(['海文州', '维拉州', '奥林州', '凯林州', '米伦州', '索伦州'], start=1):
            c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                      "VALUES (?,0,50,?,1,?)", (dn, 50 + i, now0))
        # 国籍管理部门（顶层）
        c.execute("INSERT INTO departments (name,parent_id,power_level,sort_order,is_active,created_at) "
                  "VALUES (?,0,60,90,1,?)", ('国籍管理部门', now0))
        DB._mark_migration(c, 'departments_v2')

    @staticmethod
    def _migrate_ranks_v2(c):
        """旧职级（见习~特级）→ 美式 21 档。只跑一次。"""
        if DB._migrations_done(c, 'ranks_v2'):
            return
        rank_map = {
            '见习官员': 'GS-1级文官', '九级官员': 'GS-2级文官', '八级官员': 'GS-3级文官',
            '七级官员': 'GS-4级文官', '六级官员': 'GS-5级文官', '五级官员': 'GS-6级文官',
            '四级官员': 'GS-7级文官', '三级官员': 'GS-8级文官', '二级官员': 'GS-9级文官',
            '一级官员': 'GS-10级文官', '特级官员': '高级行政官员(SES)',
        }
        for old, new in rank_map.items():
            c.execute("UPDATE officials SET rank=? WHERE rank=?", (new, old))
        c.execute("DELETE FROM rank_levels")
        _rank_seed = [
            ('GS-1级文官', 1, 0, 0, 1, 'none'), ('GS-2级文官', 1, 0, 0, 1, 'none'),
            ('GS-3级文官', 1, 0, 0, 1, 'none'), ('GS-4级文官', 1, 0, 0, 1, 'none'),
            ('GS-5级文官', 1, 0, 0, 1, 'none'), ('GS-6级文官', 1, 0, 0, 1, 'none'),
            ('GS-7级文官', 1, 0, 1, 1, 'none'), ('GS-8级文官', 1, 0, 1, 1, 'none'),
            ('GS-9级文官', 1, 0, 1, 1, 'self'), ('GS-10级文官', 1, 1, 1, 1, 'self'),
            ('GS-11级文官', 1, 1, 1, 1, 'self'), ('GS-12级文官', 1, 1, 1, 1, 'self'),
            ('GS-13级文官', 1, 1, 1, 1, 'subtree'), ('GS-14级文官', 1, 1, 1, 1, 'subtree'),
            ('GS-15级文官', 1, 1, 1, 1, 'subtree'),
            ('高级行政官员(SES)', 1, 1, 1, 1, 'subtree'),
            ('ES-5级政治任命', 1, 1, 1, 1, 'all'), ('ES-4级政治任命', 1, 1, 1, 1, 'all'),
            ('ES-3级政治任命', 1, 1, 1, 1, 'all'), ('ES-2级政治任命', 1, 1, 1, 1, 'all'),
            ('ES-1级政治任命', 1, 1, 1, 1, 'all'),
        ]
        for i, (rn, sc, ap, sd, hd, scope) in enumerate(_rank_seed, start=1):
            c.execute('''INSERT INTO rank_levels
                (name,grade,d_send_clause,d_approve,d_send_doc,d_handle,default_scope,sort_order)
                VALUES (?,?,?,?,?,?,?,?)''',
                      (rn, i, sc, ap, sd, hd, scope, i))
        DB._mark_migration(c, 'ranks_v2')

    @staticmethod
    def _migrate_positions_v2(c):
        """旧职务（部长~办事员）→ 美式职务；按官员所在部门细分部长岗位。
        只跑一次：用 _migrations 表做幂等标记（联网版下每次启动重建 40 多个职务会变成几十次云端写请求）。"""
        if DB._migrations_done(c, 'positions_v2'):
            return
        now0 = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 部长按部门细分
        pos_dept_map = {
            '国务院': '国务卿', '财政部': '财政部长', '国防部': '国防部长',
            '司法部': '司法部长', '内政部': '内政部长', '农业部': '农业部长',
            '商务部': '商务部长', '劳工部': '劳工部长',
            '卫生与公众服务部': '卫生与公众服务部长',
            '住房与城市发展部': '住房与城市发展部长', '交通部': '交通部长',
            '能源部': '能源部长', '教育部': '教育部长',
            '退伍军人事务部': '退伍军人事务部长', '国土安全部': '国土安全部长',
            '中央情报局': '中央情报局局长', '联邦调查局': '联邦调查局局长',
            '环境保护署': '环境保护署署长', '国家央行': '国家央行行长',
        }
        rows = c.execute("SELECT id,department,position FROM officials").fetchall()
        for r in rows:
            if r['position'] == '部长':
                new_pos = pos_dept_map.get(r['department'], '国务卿')
                c.execute("UPDATE officials SET position=? WHERE id=?", (new_pos, r['id']))
        # 其余职务名映射
        pos_map = {
            '副部长': '副部长', '司长': '司长', '副司长': '副司长',
            '处长': '处长', '副处长': '副处长', '科长': '科长',
            '副科长': '副科长', '办事员': '办事员', '总统': '总统',
        }
        for old, new in pos_map.items():
            c.execute("UPDATE officials SET position=? WHERE position=?", (new, old))
        # 重建职务表
        c.execute("DELETE FROM positions")
        _pos_seed = [
            # (名称, 发送条款, 审批, 发文, 办件, 审批范围, 所属部门, 部门类别)
            ('总统', 'allow', 'allow', 'allow', 'allow', 'all', '总统府', ''),
            ('副总统', 'allow', 'allow', 'allow', 'allow', 'all', '总统府', ''),
            ('国务卿', 'allow', 'allow', 'allow', 'allow', 'all', '国务院', ''),
            ('财政部长', 'allow', 'allow', 'allow', 'allow', 'all', '财政部', ''),
            ('国防部长', 'allow', 'allow', 'allow', 'allow', 'all', '国防部', ''),
            ('司法部长', 'allow', 'allow', 'allow', 'allow', 'all', '司法部', ''),
            ('内政部长', 'allow', 'allow', 'allow', 'allow', 'all', '内政部', ''),
            ('农业部长', 'allow', 'allow', 'allow', 'allow', 'all', '农业部', ''),
            ('商务部长', 'allow', 'allow', 'allow', 'allow', 'all', '商务部', ''),
            ('劳工部长', 'allow', 'allow', 'allow', 'allow', 'all', '劳工部', ''),
            ('卫生与公众服务部长', 'allow', 'allow', 'allow', 'allow', 'all', '卫生与公众服务部', ''),
            ('住房与城市发展部长', 'allow', 'allow', 'allow', 'allow', 'all', '住房与城市发展部', ''),
            ('交通部长', 'allow', 'allow', 'allow', 'allow', 'all', '交通部', ''),
            ('能源部长', 'allow', 'allow', 'allow', 'allow', 'all', '能源部', ''),
            ('教育部长', 'allow', 'allow', 'allow', 'allow', 'all', '教育部', ''),
            ('退伍军人事务部长', 'allow', 'allow', 'allow', 'allow', 'all', '退伍军人事务部', ''),
            ('国土安全部长', 'allow', 'allow', 'allow', 'allow', 'all', '国土安全部', ''),
            ('中央情报局局长', 'allow', 'allow', 'allow', 'allow', 'all', '中央情报局', ''),
            ('联邦调查局局长', 'allow', 'allow', 'allow', 'allow', 'all', '联邦调查局', ''),
            ('环境保护署署长', 'allow', 'allow', 'allow', 'allow', 'all', '环境保护署', ''),
            ('国家央行行长', 'allow', 'allow', 'allow', 'allow', 'all', '国家央行', ''),
            ('参议院议长', 'allow', 'allow', 'allow', 'allow', 'all', '议会', 'legislative'),
            ('众议院议长', 'allow', 'allow', 'allow', 'allow', 'all', '议会', 'legislative'),
            ('参议员', 'allow', 'allow', 'allow', 'allow', 'all', '议会', 'legislative'),
            ('众议员', 'allow', 'allow', 'allow', 'allow', 'all', '议会', 'legislative'),
            ('最高法院首席大法官', 'allow', 'allow', 'allow', 'allow', 'all', '最高法院', 'judicial'),
            ('最高法院大法官', 'allow', 'allow', 'allow', 'allow', 'all', '最高法院', 'judicial'),
            ('联邦上诉法官', 'allow', 'allow', 'allow', 'allow', 'all', '联邦上诉法院', 'judicial'),
            ('联邦地区法官', 'allow', 'allow', 'allow', 'allow', 'all', '联邦地区法院', 'judicial'),
            ('州长', 'allow', 'allow', 'allow', 'allow', 'subtree', '', 'state'),
            ('副州长', 'allow', 'allow', 'allow', 'allow', 'self', '', 'state'),
            ('州议员', 'allow', 'allow', 'allow', 'allow', 'self', '', 'state'),
            ('高级行政官员', 'default', 'default', 'default', 'default', 'default', '', ''),
            ('司长', 'default', 'default', 'default', 'default', 'default', '', ''),
            ('副司长', 'default', 'default', 'default', 'default', 'default', '', ''),
            ('处长', 'default', 'default', 'default', 'default', 'default', '', ''),
            ('副处长', 'default', 'default', 'default', 'default', 'default', '', ''),
            ('科长', 'default', 'default', 'default', 'default', 'default', '', ''),
            ('副科长', 'default', 'default', 'default', 'default', 'default', '', ''),
            ('办事员', 'default', 'default', 'default', 'default', 'default', '', ''),
        ]
        for i, (pn, sc, ap, sd, hd, scope, dept, kind) in enumerate(_pos_seed):
            c.execute('''INSERT INTO positions
                (name,p_send_clause,p_approve,p_send_doc,p_handle,p_scope,p_custom_depts,
                 department,dept_kind,sort_order,is_active,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (pn, sc, ap, sd, hd, scope, '', dept, kind, i, 1, now0))
        # 更新总统职级为最高（ES-1级政治任命）
        c.execute("UPDATE officials SET rank='ES-1级政治任命' WHERE position='总统'")
        DB._mark_migration(c, 'positions_v2')

    @staticmethod
    def _fill_positions_dept(c):
        """按职务名幂等填充 department / dept_kind（空值才填，保留管理员自定义）。"""
        _pos_dept_seed = {
            '总统': ('总统府', ''), '副总统': ('总统府', ''),
            '国务卿': ('国务院', ''), '财政部长': ('财政部', ''),
            '国防部长': ('国防部', ''), '司法部长': ('司法部', ''),
            '内政部长': ('内政部', ''), '农业部长': ('农业部', ''),
            '商务部长': ('商务部', ''), '劳工部长': ('劳工部', ''),
            '卫生与公众服务部长': ('卫生与公众服务部', ''),
            '住房与城市发展部长': ('住房与城市发展部', ''),
            '交通部长': ('交通部', ''), '能源部长': ('能源部', ''),
            '教育部长': ('教育部', ''), '退伍军人事务部长': ('退伍军人事务部', ''),
            '国土安全部长': ('国土安全部', ''),
            '中央情报局局长': ('中央情报局', ''), '联邦调查局局长': ('联邦调查局', ''),
            '环境保护署署长': ('环境保护署', ''), '国家央行行长': ('国家央行', ''),
            '参议院议长': ('议会', 'legislative'), '众议院议长': ('议会', 'legislative'),
            '参议员': ('议会', 'legislative'), '众议员': ('议会', 'legislative'),
            '最高法院首席大法官': ('最高法院', 'judicial'),
            '最高法院大法官': ('最高法院', 'judicial'),
            '联邦上诉法官': ('联邦上诉法院', 'judicial'),
            '联邦地区法官': ('联邦地区法院', 'judicial'),
            '州长': ('', 'state'), '副州长': ('', 'state'), '州议员': ('', 'state'),
        }
        # 一次批量提交（联网版下避免 40 多次云端往返；各行互不依赖，语义与逐条相同）
        c.executemany(
            "UPDATE positions SET department=?, dept_kind=? WHERE name=? AND (department='' OR department IS NULL)",
            [(dept, kind, pn) for pn, (dept, kind) in _pos_dept_seed.items()])

    @staticmethod
    def log(operator_type, operator_id, operator_name, action, details=''):
        try:
            conn = DB.get_conn(GOV_DB)
            conn.execute("INSERT INTO system_logs (operator_type,operator_id,operator_name,action,details,created_at) VALUES (?,?,?,?,?,?)",
                         (operator_type, operator_id, operator_name, action, details,
                          datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
            conn.close()
        except:
            pass

    # ---- 消息通知 ----
    @staticmethod
    def send_message(receiver_type, receiver_id, title, content):
        """写入站内消息。receiver_type: citizen/official/admin/all"""
        try:
            conn = DB.get_conn(GOV_DB)
            conn.execute("INSERT INTO messages (receiver_type,receiver_id,title,content,is_read,created_at) VALUES (?,?,?,?,0,?)",
                         (receiver_type, receiver_id, title, content,
                          datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
            conn.close()
            return True
        except:
            return False

    @staticmethod
    def unread_count(receiver_type, receiver_id):
        try:
            conn = DB.get_conn(GOV_DB)
            n = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE is_read=0 AND (receiver_type=? AND receiver_id=?) OR is_read=0 AND receiver_type='all'",
                (receiver_type, receiver_id)).fetchone()[0]
            conn.close()
            return n
        except:
            return 0

    # ---- 户籍对接 ----
    # ================= 权限引擎 =================
    @staticmethod
    def active_departments():
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT name FROM departments WHERE is_active=1 ORDER BY sort_order,id").fetchall()
        conn.close()
        names = [r['name'] for r in rows]
        return names if names else list(DEPARTMENTS)

    @staticmethod
    def all_departments():
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute('''SELECT d.*, p.name AS parent_name FROM departments d
                               LEFT JOIN departments p ON p.id=d.parent_id
                               ORDER BY d.sort_order,d.id''').fetchall()
        conn.close()
        return rows

    @staticmethod
    def all_ranks():
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM rank_levels ORDER BY grade").fetchall()
        conn.close()
        return rows

    @staticmethod
    def _dept_parent_map(c):
        pmap = {}
        for r in c.execute('''SELECT d.name AS cname, p.name AS pname FROM departments d
                              LEFT JOIN departments p ON p.id=d.parent_id''').fetchall():
            pmap[r['cname']] = r['pname']
        return pmap

    @staticmethod
    def dept_is_descendant(dept_name, ancestor_name):
        """dept_name 是否严格位于 ancestor_name 之下（不含相等）。"""
        if not dept_name or not ancestor_name or dept_name == ancestor_name:
            return False
        conn = DB.get_conn(GOV_DB)
        pmap = DB._dept_parent_map(conn.cursor())
        conn.close()
        cur = pmap.get(dept_name)
        seen = set()
        while cur and cur not in seen:
            if cur == ancestor_name:
                return True
            seen.add(cur)
            cur = pmap.get(cur)
        return False

    @staticmethod
    def official_perm(official):
        """计算某官员的最终权限信息。official 为 officials 表行（dict/Row）。
        权限合并优先级：个人覆盖 allow/deny ＞ 职务设置(default/allow/deny) ＞ 职级默认。
        审批范围：个人覆盖 ＞ 职务设置(非 default) ＞ 职级默认范围。"""
        oid = official['id']
        dept_name = official['department'] or ''
        rank_name = official['rank'] or ''
        pos_name = official['position'] or ''
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        rk = c.execute("SELECT * FROM rank_levels WHERE name=?", (rank_name,)).fetchone()
        base = {'send_clause': 1, 'approve': 0, 'send_doc': 0, 'handle': 1}
        scope = 'none'
        if rk:
            for k, col in PERM_RANK_COL.items():
                base[k] = rk[col] or 0
            scope = rk['default_scope'] or 'none'
        # 职务层（停用职务对已任该职官员仍生效；停用仅影响新增/调整时的可选项）
        pos = c.execute("SELECT * FROM positions WHERE name=?", (pos_name,)).fetchone()
        pos_modes = {}
        pos_scope = 'default'
        pos_custom = []
        after_pos = dict(base)
        if pos:
            for k, col in PERM_POS_COL.items():
                mv = (pos[col] or 'default')
                pos_modes[k] = mv
                if mv == 'allow':
                    after_pos[k] = 1
                elif mv == 'deny':
                    after_pos[k] = 0
            pos_scope = pos['p_scope'] or 'default'
            pos_custom = [x.strip() for x in (pos['p_custom_depts'] or '').split(',') if x.strip()]
            if pos_scope != 'default':
                scope = pos_scope
        dept = c.execute("SELECT * FROM departments WHERE name=?", (dept_name,)).fetchone()
        overrides = {}
        for r in c.execute("SELECT perm_key,mode FROM official_perm_overrides WHERE official_id=?",
                           (oid,)).fetchall():
            overrides[r['perm_key']] = r['mode']
        eff = {}
        for k in PERM_KEYS:
            if k in overrides:
                eff[k] = 1 if overrides[k] == 'allow' else 0
            else:
                eff[k] = bool(after_pos[k])
        custom = list(pos_custom)
        so = c.execute("SELECT scope,custom_depts FROM official_scope_overrides WHERE official_id=?",
                       (oid,)).fetchone()
        if so:
            scope = so['scope']
            custom = [x.strip() for x in (so['custom_depts'] or '').split(',') if x.strip()]
        if not eff['approve']:
            scope = 'none'
        # ===== V2 美式权力点合成（44 项）=====
        # 取该职级默认授予
        rank_pows = {}
        if rk:
            for r in c.execute("SELECT power_key,value FROM rank_powers WHERE rank_id=?",
                               (rk['id'],)).fetchall():
                rank_pows[r['power_key']] = bool(r['value'])
        # 取该职务对权力点的覆盖
        pos_pows = {}
        if pos:
            for r in c.execute("SELECT power_key,mode FROM position_powers WHERE position_id=?",
                               (pos['id'],)).fetchall():
                pos_pows[r['power_key']] = r['mode']
        powers = {}
        for r in c.execute("SELECT pkey FROM power_catalog ORDER BY sort_order").fetchall():
            pk = r['pkey']
            if pk in overrides:
                powers[pk] = 1 if overrides[pk] == 'allow' else 0
            elif pk in pos_pows:
                m = pos_pows[pk]
                powers[pk] = 1 if m == 'allow' else (0 if m == 'deny' else bool(rank_pows.get(pk)))
            else:
                powers[pk] = bool(rank_pows.get(pk))
        # 旧 4 项以 eff 为准（保留原有逻辑），其余并入 powers
        full_eff = dict(powers)
        for k in PERM_KEYS:
            full_eff[k] = bool(eff[k])
        conn.close()
        return {'base': base, 'overrides': overrides, 'eff': full_eff, 'scope': scope,
                'custom_depts': custom, 'grade': rk['grade'] if rk else None,
                'rank_name': rank_name, 'dept_name': dept_name,
                'dept_level': dept['power_level'] if dept else None,
                'pos_name': pos_name, 'pos_modes': pos_modes, 'pos_scope': pos_scope,
                'powers': powers}

    @staticmethod
    def can(official, perm_key):
        """便捷判定：official 行 + 权限 key -> bool。非在职官员一律 False。"""
        try:
            if not official:
                return False
            if official.get('status') and official['status'] != '在职':
                return False
            return bool(DB.official_perm(official)['eff'].get(perm_key))
        except Exception:
            return False

    @staticmethod
    def scope_department_set(pinfo):
        """返回该官员可审批的部门名集合；None 表示全部；空集合表示无。"""
        scope = pinfo['scope']
        dept = pinfo['dept_name']
        if scope == 'all':
            return None
        if scope == 'none' or not dept:
            return set()
        if scope == 'self':
            return {dept}
        if scope == 'custom':
            return set(pinfo['custom_depts'])
        if scope == 'subtree':
            conn = DB.get_conn(GOV_DB)
            pmap = DB._dept_parent_map(conn.cursor())
            conn.close()
            result = {dept}
            for name in pmap:
                if DB.dept_is_descendant(name, dept):
                    result.add(name)
            return result
        return set()

    @staticmethod
    def can_approve_department(official, target_dept, pinfo=None):
        if not DB.can(official, 'approve'):
            return False
        pinfo = pinfo or DB.official_perm(official)
        allowed = DB.scope_department_set(pinfo)
        if allowed is None:
            return True
        if not target_dept:
            # 未指定承办部门的旧单据，仅“全部部门”权限可审批
            return False
        return target_dept in allowed

    # ---------- 权限配置写入（后台用） ----------
    @staticmethod
    def upsert_department(name, parent_name, power_level, head_name, sort_order, is_active, did=None):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        parent_id = 0
        if parent_name:
            r = c.execute("SELECT id FROM departments WHERE name=?", (parent_name,)).fetchone()
            parent_id = r['id'] if r else 0
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if did:
            c.execute('''UPDATE departments SET name=?,parent_id=?,power_level=?,head_name=?,
                sort_order=?,is_active=? WHERE id=?''',
                      (name, parent_id, int(power_level), head_name, int(sort_order),
                       1 if is_active else 0, did))
        else:
            c.execute('''INSERT INTO departments (name,parent_id,power_level,head_name,sort_order,is_active,created_at)
                VALUES (?,?,?,?,?,?,?)''',
                      (name, parent_id, int(power_level), head_name, int(sort_order),
                       1 if is_active else 0, now))
        conn.commit()
        conn.close()

    @staticmethod
    def save_rank_defaults(rid, grade, name, d_send_clause, d_approve, d_send_doc, d_handle, scope):
        conn = DB.get_conn(GOV_DB)
        conn.execute('''UPDATE rank_levels SET grade=?,name=?,d_send_clause=?,d_approve=?,d_send_doc=?,
            d_handle=?,default_scope=? WHERE id=?''',
                     (int(grade), name, 1 if d_send_clause else 0, 1 if d_approve else 0,
                      1 if d_send_doc else 0, 1 if d_handle else 0, scope, rid))
        conn.commit()
        conn.close()

    @staticmethod
    def set_official_override(oid, perm_key, mode):
        """mode: 'allow' / 'deny' / None(删除覆盖，恢复默认)"""
        conn = DB.get_conn(GOV_DB)
        if mode in ('allow', 'deny'):
            conn.execute('''INSERT INTO official_perm_overrides (official_id,perm_key,mode) VALUES (?,?,?)
                ON CONFLICT(official_id,perm_key) DO UPDATE SET mode=excluded.mode''',
                         (oid, perm_key, mode))
        else:
            conn.execute("DELETE FROM official_perm_overrides WHERE official_id=? AND perm_key=?",
                         (oid, perm_key))
        conn.commit()
        conn.close()

    @staticmethod
    def set_official_scope(oid, scope, custom_depts=None):
        conn = DB.get_conn(GOV_DB)
        if scope in ('self', 'subtree', 'custom', 'all'):
            conn.execute('''INSERT INTO official_scope_overrides (official_id,scope,custom_depts) VALUES (?,?,?)
                ON CONFLICT(official_id) DO UPDATE SET scope=excluded.scope,custom_depts=excluded.custom_depts''',
                         (oid, scope, ','.join(custom_depts or [])))
        else:
            conn.execute("DELETE FROM official_scope_overrides WHERE official_id=?", (oid,))
        conn.commit()
        conn.close()

    @staticmethod
    def reset_official_perms(oid):
        conn = DB.get_conn(GOV_DB)
        conn.execute("DELETE FROM official_perm_overrides WHERE official_id=?", (oid,))
        conn.execute("DELETE FROM official_scope_overrides WHERE official_id=?", (oid,))
        conn.execute("DELETE FROM official_power_overrides WHERE official_id=?", (oid,))
        conn.commit()
        conn.close()

    # ---------- 职务管理 ----------
    @staticmethod
    def active_positions():
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT name FROM positions WHERE is_active=1 ORDER BY sort_order,id").fetchall()
        conn.close()
        names = [r['name'] for r in rows]
        return names if names else list(POSITIONS)

    @staticmethod
    def all_positions():
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM positions ORDER BY sort_order,id").fetchall()
        conn.close()
        return rows

    @staticmethod
    def dept_kind_of(dept_name):
        """部门类别：'' 普通 / 'legislative' 立法 / 'judicial' 司法 / 'state' 州。"""
        if not dept_name:
            return ''
        dept_name = str(dept_name)
        if dept_name.endswith('州'):
            return 'state'
        if dept_name in ('议会', '参议院', '众议院'):
            return 'legislative'
        if dept_name in ('最高法院', '联邦上诉法院', '联邦地区法院'):
            return 'judicial'
        return ''

    @staticmethod
    def position_matches_dept(position, dept_name):
        """职务是否允许在该部门任职：指定部门精确匹配 / 类别匹配 / 通用放行。"""
        pos_dept = (position.get('department') or '') if isinstance(position, dict) else ''
        pos_kind = (position.get('dept_kind') or '') if isinstance(position, dict) else ''
        dept_kind = DB.dept_kind_of(dept_name)
        if pos_dept:
            if pos_dept == (dept_name or ''):
                return True
            # 类别一致（议会系职务可在任何立法部门任职，如参议员可在参议院/众议院）
            if pos_kind and dept_kind and pos_kind == dept_kind:
                return True
            return False
        if pos_kind:
            return dept_kind == pos_kind
        return True

    @staticmethod
    def positions_for_dept(dept_name):
        """某部门可选的职位：本部门职位 + 类别匹配职位 + 通用职位。"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM positions WHERE is_active=1 ORDER BY sort_order,id").fetchall()
        conn.close()
        out = []
        for r in rows:
            if DB.position_matches_dept(dict(r), dept_name):
                out.append(r['name'])
        return out if out else [p['name'] for p in rows]

    @staticmethod
    def validate_position_dept(position_name, dept_name):
        """职务是否允许在该部门任职（False=不允许）。未知职务放行（兼容旧数据）。"""
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT * FROM positions WHERE name=?", (position_name,)).fetchone()
        conn.close()
        if not r:
            return True
        return DB.position_matches_dept(dict(r), dept_name)

    @staticmethod
    def position_slot_occupied(position, department='', exclude_official_id=None):
        """职务唯一名额检查：总统/副总统/各部部长全国唯一；处长按部门唯一（每部门最多一名）。
        返回 (是否被占用, 占用者姓名)。"""
        position = (position or '').strip()
        if not position:
            return False, None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        holder = None
        if position in ('总统', '副总统') or (position.endswith('部长') and position != '副部长'):
            sql = "SELECT name FROM officials WHERE position=? AND status='在职'"
            args = [position]
            if exclude_official_id:
                sql += " AND id!=?"
                args.append(int(exclude_official_id))
            r = c.execute(sql + " LIMIT 1", args).fetchone()
            holder = r['name'] if r else None
        elif position == '处长':
            sql = "SELECT name FROM officials WHERE position='处长' AND department=? AND status='在职'"
            args = [department or '']
            if exclude_official_id:
                sql += " AND id!=?"
                args.append(int(exclude_official_id))
            r = c.execute(sql + " LIMIT 1", args).fetchone()
            holder = r['name'] if r else None
        conn.close()
        return (True, holder) if holder else (False, None)

    @staticmethod
    def dept_positions(dept_name):
        """部门管理页：本部门可任职务（含通用）+ 纯通用职位（可绑定到本部门）。"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM positions WHERE is_active=1 ORDER BY sort_order,id").fetchall()
        conn.close()
        bound, general = [], []
        for r in rows:
            if DB.position_matches_dept(dict(r), dept_name):
                bound.append(r['name'])
            if not r['department'] and not r['dept_kind']:
                general.append(r['name'])
        return bound, general

    @staticmethod
    def bind_position_dept(position_name, dept_name):
        """把职务绑定到指定部门（解绑其他部门）。dept_name='' 表示设为通用。"""
        conn = DB.get_conn(GOV_DB)
        if dept_name:
            conn.execute("UPDATE positions SET department=?, dept_kind='' WHERE name=?",
                         (dept_name, position_name))
        else:
            conn.execute("UPDATE positions SET department='', dept_kind='' WHERE name=?", (position_name,))
        conn.commit()
        conn.close()

    @staticmethod
    def get_position(pid):
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
        conn.close()
        return r

    @staticmethod
    def position_in_use(name, exclude_id=None):
        conn = DB.get_conn(GOV_DB)
        q = "SELECT COUNT(*) FROM officials WHERE position=?"
        params = [name]
        if exclude_id:
            q += " AND 1=1"
        n = conn.execute(q, params).fetchone()[0]
        conn.close()
        return n > 0

    @staticmethod
    def save_position(name, m_send_clause, m_approve, m_send_doc, m_handle,
                      scope, custom_depts, sort_order, is_active, pid=None,
                      department=None, dept_kind=None):
        """新增/更新职务。返回 (ok, msg)。department/dept_kind 为 None 时更新保留原值。"""
        name = (name or '').strip()
        if not name:
            return False, '职务名称不能为空'
        for mv in (m_send_clause, m_approve, m_send_doc, m_handle):
            if mv not in ('default', 'allow', 'deny'):
                return False, '权限取值非法'
        if scope not in ('default', 'none', 'self', 'subtree', 'custom', 'all'):
            return False, '审批范围非法'
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        dup = c.execute("SELECT id FROM positions WHERE name=?", (name,)).fetchone()
        if dup and (pid is None or dup['id'] != int(pid)):
            conn.close()
            return False, f'已存在同名职务“{name}”'
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if pid:
            sets = ("name=?,p_send_clause=?,p_approve=?,p_send_doc=?,p_handle=?,"
                    "p_scope=?,p_custom_depts=?,sort_order=?,is_active=?")
            args = [name, m_send_clause, m_approve, m_send_doc, m_handle, scope,
                    ','.join(custom_depts or []), int(sort_order), 1 if is_active else 0]
            if department is not None:
                sets += ",department=?"; args.append(department)
            if dept_kind is not None:
                sets += ",dept_kind=?"; args.append(dept_kind)
            args.append(int(pid))
            c.execute("UPDATE positions SET %s WHERE id=?" % sets, args)
        else:
            dept = department if department is not None else ''
            kind = dept_kind if dept_kind is not None else ''
            c.execute('''INSERT INTO positions
                (name,p_send_clause,p_approve,p_send_doc,p_handle,p_scope,p_custom_depts,
                 department,dept_kind,sort_order,is_active,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (name, m_send_clause, m_approve, m_send_doc, m_handle, scope,
                       ','.join(custom_depts or []), dept, kind, int(sort_order),
                       1 if is_active else 0, now))
        conn.commit()
        conn.close()
        return True, '职务已保存'

    @staticmethod
    def delete_position(pid):
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT name FROM positions WHERE id=?", (pid,)).fetchone()
        if not r:
            conn.close()
            return False, '职务不存在'
        used = conn.execute("SELECT COUNT(*) FROM officials WHERE position=?", (r['name'],)).fetchone()[0]
        if used > 0:
            conn.close()
            return False, f'该职务正被 {used} 名官员使用，不能删除；可改为“停用”'
        conn.execute("DELETE FROM positions WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        return True, '职务已删除'

    # ================= 会议投票模块 =================
    @staticmethod
    def can_convene_meeting(official):
        """召开会议权限：总统府的总统，或各部门部长。"""
        try:
            pos = (official.get('position') or '').strip()
            dept = (official.get('department') or '').strip()
            if pos == '总统' and dept == '总统府':
                return True
            if pos == '部长':
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def active_officials():
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute(
            "SELECT * FROM officials WHERE status='在职' "
            "ORDER BY CASE WHEN position='副部长' THEN 32 "
            "ELSE COALESCE((SELECT sort_order FROM positions WHERE name=officials.position),999) END, "
            "department, id").fetchall()
        conn.close()
        return rows

    @staticmethod
    def pending_files_for_convener(official):
        """该召开人“需要其审批”的待审批文件（按审批范围过滤）。"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute(
            "SELECT * FROM approval_files WHERE status='待审批' ORDER BY id DESC").fetchall()
        conn.close()
        out = []
        for r in rows:
            if DB.can_approve_department(official, r['target_department']):
                out.append(r)
        return out

    @staticmethod
    def create_meeting(title, content, official, approval_id, participant_ids):
        """返回 (ok, msg, meeting_id)。"""
        title = (title or '').strip()
        if not title:
            return False, '会议议题不能为空', None
        try:
            pids = [int(x) for x in dict.fromkeys(participant_ids)]
        except Exception:
            return False, '参会人员数据有误', None
        if not pids:
            return False, '请至少选择一名参会人员', None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        # 召开人必须在参会名单内（其也可投票；不强制，但默认包含）
        if int(official['id']) not in pids:
            pids.append(int(official['id']))
        approval_title = ''
        if approval_id:
            ar = c.execute("SELECT * FROM approval_files WHERE id=? AND status='待审批'",
                           (int(approval_id),)).fetchone()
            if not ar:
                conn.close()
                return False, '所选审批文件不存在或已处理', None
            if not DB.can_approve_department(official, ar['target_department']):
                conn.close()
                return False, '该文件不在您的审批范围内，不能关联此会议', None
            approval_title = ar['title']
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('''INSERT INTO meetings
            (title,content,convener_id,convener_name,approval_id,approval_title,status,
             total_count,created_at) VALUES (?,?,?,?,?,?,'投票中',?,?)''',
                  (title, content.strip(), int(official['id']), official['name'],
                   int(approval_id) if approval_id else None, approval_title, len(pids), now))
        mid = c.lastrowid
        for pid in pids:
            c.execute("INSERT OR IGNORE INTO meeting_participants (meeting_id,official_id) VALUES (?,?)",
                      (mid, pid))
        conn.commit()
        conn.close()
        DB.log('official', official['id'], official['name'], '召开会议',
               f'《{title}》参会{len(pids)}人 关联审批={"有" if approval_id else "无"}')
        return True, '会议已创建，参会人员现在可以投票', mid

    @staticmethod
    def meeting_participant_ids(mid):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT official_id FROM meeting_participants WHERE meeting_id=?",
                            (mid,)).fetchall()
        conn.close()
        return [r['official_id'] for r in rows]

    @staticmethod
    def is_participant(mid, oid):
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT 1 FROM meeting_participants WHERE meeting_id=? AND official_id=?",
                         (mid, oid)).fetchone()
        conn.close()
        return r is not None

    @staticmethod
    def has_voted(mid, oid):
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT 1 FROM meeting_votes WHERE meeting_id=? AND official_id=?",
                         (mid, oid)).fetchone()
        conn.close()
        return r is not None

    @staticmethod
    def cast_vote(mid, oid, voter_name, option, comment):
        if option not in ('赞成', '反对', '弃权'):
            return False, '投票选项无效'
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        mt = c.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        if not mt:
            conn.close(); return False, '会议不存在'
        if mt['status'] != '投票中':
            conn.close(); return False, '投票已结束'
        if not c.execute("SELECT 1 FROM meeting_participants WHERE meeting_id=? AND official_id=?",
                         (mid, oid)).fetchone():
            conn.close(); return False, '您不是本次会议的参会人员'
        try:
            c.execute('''INSERT INTO meeting_votes (meeting_id,official_id,voter_name,option,comment,created_at)
                VALUES (?,?,?,?,?,?)''',
                      (mid, oid, voter_name, option, comment.strip(),
                       datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        except sqlite3.IntegrityError:
            conn.close(); return False, '您已经投过票了'
        conn.commit()
        conn.close()
        DB.log('official', oid, voter_name, '会议投票', f'会议《{mt["title"]}》投：{option}')
        return True, '投票成功'

    @staticmethod
    def meeting_vote_stats(mid):
        """返回统计 dict：yes/no/abstain/total/voted/unvoted 及明细行。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        mt = c.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        total = c.execute("SELECT COUNT(*) FROM meeting_participants WHERE meeting_id=?", (mid,)).fetchone()[0]
        agg = {x[0]: x[1] for x in c.execute(
            "SELECT option,COUNT(*) FROM meeting_votes WHERE meeting_id=? GROUP BY option", (mid,)).fetchall()}
        details = c.execute('''SELECT v.*, o.department, o.position FROM meeting_votes v
            LEFT JOIN officials o ON o.id=v.official_id WHERE v.meeting_id=? ORDER BY v.id''',
                            (mid,)).fetchall()
        conn.close()
        yes = agg.get('赞成', 0); no = agg.get('反对', 0); ab = agg.get('弃权', 0)
        return {'meeting': mt, 'yes': yes, 'no': no, 'abstain': ab,
                'total': total, 'voted': yes + no + ab, 'unvoted': total - yes - no - ab,
                'details': details}

    @staticmethod
    def recalc_and_close(mid):
        """结束投票并把统计写回 meetings。"""
        st = DB.meeting_vote_stats(mid)
        conn = DB.get_conn(GOV_DB)
        conn.execute('''UPDATE meetings SET status='已结束', yes_count=?, no_count=?, abstain_count=?,
            total_count=?, closed_at=? WHERE id=?''',
                     (st['yes'], st['no'], st['abstain'], st['total'],
                      datetime.now().strftime('%Y-%m-%d %H:%M:%S'), mid))
        conn.commit(); conn.close()
        return st

    @staticmethod
    def reopen_meeting(mid):
        conn = DB.get_conn(GOV_DB)
        conn.execute("UPDATE meetings SET status='投票中', final_decision='', decision_comment='', "
                     "decided_at='' WHERE id=?", (mid,))
        conn.commit(); conn.close()

    @staticmethod
    def decide_meeting(mid, convener, decision, comment):
        """召开人根据投票结果对关联文件做最终审批（通过/驳回）；无关联文件则仅记录结论。"""
        if decision not in ('已通过', '已驳回'):
            return False, '审批结论无效'
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        mt = c.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        if not mt:
            conn.close(); return False, '会议不存在'
        if mt['convener_id'] != int(convener['id']):
            conn.close(); return False, '只有会议召开人可以做出审批'
        if mt['approval_id']:
            ar = c.execute("SELECT * FROM approval_files WHERE id=?", (mt['approval_id'],)).fetchone()
            if not ar:
                conn.close(); return False, '关联审批文件不存在'
            if ar['status'] != '待审批':
                conn.close(); return False, f'关联文件已被处理（{ar["status"]}）'
            if not DB.can_approve_department(convener, ar['target_department']):
                conn.close(); return False, '该文件不在您的审批范围内'
            c.execute('''UPDATE approval_files SET status=?,approver_id=?,approver_name=?,
                approve_comment=?,approved_at=? WHERE id=?''',
                      (decision, str(convener['id']), convener['name'],
                       f'经会议《{mt["title"]}》投票后审批：{comment or "同意"}',
                       datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ar['id']))
            DB.send_message('citizen', ar['applicant_id'], '审批结果通知',
                            f'您提交的文件《{ar["title"]}》经会议审议后审批结果：{decision}。')
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('''UPDATE meetings SET status='已审批', final_decision=?, decision_comment=?, decided_at=?
            WHERE id=?''', (decision, comment.strip(), now, mid))
        conn.commit(); conn.close()
        DB.log('official', convener['id'], convener['name'], '会议审批',
               f'会议《{mt["title"]}》结论：{decision}')
        return True, f'已按投票结果做出审批：{decision}'

    @staticmethod
    def meetings_for_official(oid, as_convener=False):
        conn = DB.get_conn(GOV_DB)
        if as_convener:
            rows = conn.execute("SELECT * FROM meetings WHERE convener_id=? ORDER BY id DESC",
                                (oid,)).fetchall()
        else:
            rows = conn.execute('''SELECT DISTINCT m.* FROM meetings m
                JOIN meeting_participants p ON p.meeting_id=m.id
                WHERE p.official_id=? ORDER BY m.id DESC''', (oid,)).fetchall()
        conn.close()
        return rows

    @staticmethod
    def get_meeting(mid):
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        conn.close()
        return r

    @staticmethod
    def verify_household(id_number, name):
        try:
            conn = DB.get_conn(HOUSEHOLD_DB)
            row = conn.execute("SELECT * FROM residents WHERE id_number=? AND name=? AND status='正常'",
                                (id_number, name)).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            messagebox.showerror('数据库错误', f'无法连接户籍系统数据库：{e}')
            return None

    @staticmethod
    def get_household_info(id_number):
        try:
            conn = DB.get_conn(HOUSEHOLD_DB)
            row = conn.execute("SELECT * FROM residents WHERE id_number=?", (id_number,)).fetchone()
            conn.close()
            return dict(row) if row else None
        except:
            return None

    # ---- 犯罪记录对接（无犯罪记录证明）----
    @staticmethod
    def get_criminal_records(id_number):
        try:
            conn = DB.get_conn(HOUSEHOLD_DB)
            rows = conn.execute(
                "SELECT case_name, case_type, case_date, handling_org, punishment FROM criminal_records WHERE id_number=?",
                (id_number,)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except:
            return None

    # ---- 法院对接 ----
    @staticmethod
    def create_court_case(plaintiff_name, plaintiff_id, defendant_name, defendant_id,
                          case_title, case_type, description, request):
        try:
            conn = DB.get_conn(COURT_DB)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM cases")
            count = c.fetchone()[0] + 1
            case_number = f'(2026)蛋法行初字第{count:04d}号'
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            c.execute('''INSERT INTO cases (case_number,case_type,case_title,plaintiff_name,plaintiff_id,
                defendant_name,defendant_id,case_description,lawsuit_request,status,file_time,fee,payment_status,
                judge_id,judge_name,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (case_number, case_type, case_title, plaintiff_name, plaintiff_id,
                 defendant_name, defendant_id, description, request, 'trial', now, 0, 'paid',
                 1, '袁泓涵', 1))
            case_id = c.lastrowid
            c.execute('''INSERT INTO parties (case_number,party_type,name,id_card,identity_verified,verify_time)
                VALUES (?,?,?,?,?,?)''', (case_number, 'plaintiff', plaintiff_name, plaintiff_id, 1, now))
            c.execute('''INSERT INTO parties (case_number,party_type,name,id_card,identity_verified,verify_time)
                VALUES (?,?,?,?,?,?)''', (case_number, 'defendant', defendant_name, defendant_id, 1, now))
            c.execute('''INSERT INTO operation_logs (operator_type,operator_id,operator_name,operation_type,details,operation_time)
                VALUES (?,?,?,?,?,?)''', ('admin', 1, '政务系统', '提起诉讼',
                f'政务系统起诉官员{defendant_name}，案号{case_number}', now))
            conn.commit()
            conn.close()
            return case_number, case_id
        except Exception as e:
            messagebox.showerror('法院系统错误', f'无法对接法院系统：{e}')
            return None, None

    # ================= V2：党派管理 =================
    @staticmethod
    def list_parties(active_only=True):
        conn = DB.get_conn(GOV_DB)
        sql = "SELECT * FROM parties"
        if active_only:
            sql += " WHERE is_active=1"
        rows = conn.execute(sql + " ORDER BY id").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def create_party(name, short_name, leader_id, leader_name, platform):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            now = now_str()
            # 一人一党：党魁若已属其他党派则拒绝建党
            if leader_id:
                exist = c.execute('''SELECT p.id,p.name FROM party_members m JOIN parties p ON p.id=m.party_id
                    WHERE m.official_id=? AND p.is_active=1''', (leader_id,)).fetchone()
                if exist:
                    conn.close()
                    return False, f'{leader_name} 已属《{exist["name"]}》，请先退出原党派', None
            c.execute('''INSERT INTO parties (name,short_name,leader_id,leader_name,platform,created_at,is_active)
                         VALUES (?,?,?,?,?,?,1)''',
                      (name, short_name, leader_id, leader_name, platform, now))
            pid = c.lastrowid
            # 党魁自动入党
            if leader_id:
                c.execute('''INSERT OR IGNORE INTO party_members (party_id,official_id,official_name,joined_at)
                             VALUES (?,?,?,?)''', (pid, leader_id, leader_name, now))
            conn.commit()
            conn.close()
            return True, '建党成功', pid
        except sqlite3.IntegrityError:
            conn.close()
            return False, '党派名称已存在', None

    @staticmethod
    def party_members(party_id):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute('''SELECT m.*, o.department, o.position, o.rank,
            COALESCE(NULLIF(m.citizen_name,''), m.official_name) AS show_name,
            COALESCE(NULLIF(m.citizen_id_number,''), CAST(o.id AS TEXT)) AS show_id,
            CASE WHEN m.applicant_type='citizen' THEN '公民' ELSE '官员' END AS member_kind
            FROM party_members m LEFT JOIN officials o ON o.id=m.official_id
            WHERE m.party_id=? ORDER BY m.id''', (party_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def join_party(party_id, official):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            now = now_str()
            # 一人一党
            exist = c.execute('''SELECT p.id,p.name FROM party_members m JOIN parties p ON p.id=m.party_id
                WHERE m.official_id=? AND p.is_active=1''', (official['id'],)).fetchone()
            if exist and exist['id'] != party_id:
                conn.close()
                return False, f'{official["name"]} 已属《{exist["name"]}》，请先退出原党派'
            c.execute('''INSERT OR IGNORE INTO party_members (party_id,official_id,official_name,joined_at)
                         VALUES (?,?,?,?)''', (party_id, official['id'], official['name'], now))
            conn.commit()
            conn.close()
            return True, '加入党派成功'
        except Exception as e:
            conn.close()
            return False, f'加入失败：{e}'

    @staticmethod
    def leave_party(party_id, official_id):
        conn = DB.get_conn(GOV_DB)
        conn.execute("DELETE FROM party_members WHERE party_id=? AND official_id=?", (party_id, official_id))
        conn.commit()
        conn.close()
        return True, '已退出党派'

    @staticmethod
    def set_party_leader(party_id, leader_id, leader_name):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        now = now_str()
        # 一人一党：新党魁若属其他党派则拒绝
        if leader_id:
            exist = c.execute('''SELECT p.id,p.name FROM party_members m JOIN parties p ON p.id=m.party_id
                WHERE m.official_id=? AND p.is_active=1 AND p.id!=?''', (leader_id, party_id)).fetchone()
            if exist:
                conn.close()
                return False, f'{leader_name} 已属《{exist["name"]}》，不能兼任两党党魁'
        c.execute("UPDATE parties SET leader_id=?, leader_name=? WHERE id=?",
                  (leader_id, leader_name, party_id))
        c.execute('''INSERT OR IGNORE INTO party_members (party_id,official_id,official_name,joined_at)
                     VALUES (?,?,?,?)''', (party_id, leader_id, leader_name, now))
        conn.commit()
        conn.close()
        return True, '党魁已更新'

    @staticmethod
    def delete_party(party_id):
        conn = DB.get_conn(GOV_DB)
        conn.execute("DELETE FROM party_members WHERE party_id=?", (party_id,))
        conn.execute("DELETE FROM parties WHERE id=?", (party_id,))
        conn.commit()
        conn.close()
        return True, '党派已解散'

    @staticmethod
    def official_party(official_id):
        """某官员所属党派（含党魁标记）"""
        conn = DB.get_conn(GOV_DB)
        row = conn.execute('''SELECT p.id,p.name,p.short_name,p.leader_id FROM party_members m
            JOIN parties p ON p.id=m.party_id WHERE m.official_id=? AND p.is_active=1
            ORDER BY m.id LIMIT 1''', (official_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def party_by_citizen(id_number):
        """某公民所属党派（含党魁标记）"""
        conn = DB.get_conn(GOV_DB)
        row = conn.execute('''SELECT p.id,p.name,p.short_name,p.leader_id FROM party_members m
            JOIN parties p ON p.id=m.party_id WHERE m.citizen_id_number=? AND p.is_active=1
            ORDER BY m.id LIMIT 1''', (id_number,)).fetchone()
        conn.close()
        return dict(row) if row else None

    # ============ V2.1 党派端：入党申请流 ============
    @staticmethod
    def apply_party(party_id, official):
        """官员申请入党（待党魁审批）；一人一党"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            now = now_str()
            exist = c.execute('''SELECT p.name FROM party_members m JOIN parties p ON p.id=m.party_id
                WHERE m.official_id=? AND p.is_active=1''', (official['id'],)).fetchone()
            if exist:
                conn.close()
                return False, f'您已属《{exist["name"]}》，一人只能加入一个党派'
            c.execute('''INSERT INTO party_requests (party_id,official_id,official_name,status,created_at)
                         VALUES (?,?,?, '待审批', ?)''', (party_id, official['id'], official['name'], now))
            conn.commit()
            conn.close()
            return True, '入党申请已提交，等待党魁审批'
        except sqlite3.IntegrityError:
            conn.close()
            return False, '您已有待审批的入党申请，或已是该党党员'

    @staticmethod
    def my_party_requests(official_id):
        """某官员自己的入党申请记录"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute('''SELECT r.*, p.name AS party_name FROM party_requests r
            JOIN parties p ON p.id=r.party_id WHERE r.official_id=? ORDER BY r.id DESC''',
                            (official_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def my_party_requests_citizen(id_number):
        """某公民自己的入党申请记录"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute('''SELECT r.*, p.name AS party_name FROM party_requests r
            JOIN parties p ON p.id=r.party_id WHERE r.citizen_id_number=? ORDER BY r.id DESC''',
                            (id_number,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def apply_party_citizen(party_id, id_number, name):
        """公民申请入党（待党魁审批）；一人一党"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        now = now_str()
        try:
            exist = c.execute('''SELECT p.name FROM party_members m JOIN parties p ON p.id=m.party_id
                WHERE m.citizen_id_number=? AND p.is_active=1''', (id_number,)).fetchone()
            if exist:
                conn.close()
                return False, f'您已属《{exist["name"]}》，一人只能加入一个党派'
            dup = c.execute("SELECT id FROM party_requests WHERE party_id=? AND citizen_id_number=? "
                            "AND status='待审批'", (party_id, id_number)).fetchone()
            if dup:
                conn.close()
                return False, '您已有待审批的入党申请'
            c.execute('''INSERT INTO party_requests (party_id,official_id,official_name,applicant_type,
                         citizen_id_number,citizen_name,status,created_at)
                         VALUES (?,0,'', 'citizen', ?, ?, '待审批', ?)''', (party_id, id_number, name, now))
            conn.commit()
            conn.close()
            return True, '入党申请已提交，等待党魁审批'
        except sqlite3.IntegrityError:
            conn.close()
            return False, '申请提交失败，请重试'

    @staticmethod
    def pending_party_requests(party_id):
        """党魁查看本党待审批入党申请（官员+公民）"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute('''SELECT r.*, o.department, o.position, o.rank,
            COALESCE(NULLIF(r.citizen_name,''), r.official_name) AS show_name,
            COALESCE(NULLIF(r.citizen_id_number,''), CAST(o.id AS TEXT)) AS show_id,
            CASE WHEN r.applicant_type='citizen' THEN '公民' ELSE '官员' END AS applicant_kind
            FROM party_requests r LEFT JOIN officials o ON o.id=r.official_id
            WHERE r.party_id=? AND r.status='待审批' ORDER BY r.id''', (party_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def approve_party_request(req_id, actor_name=''):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        r = c.execute("SELECT * FROM party_requests WHERE id=?", (req_id,)).fetchone()
        if not r or r['status'] != '待审批':
            conn.close()
            return False, '申请不存在或已处理'
        now = now_str()
        if r['applicant_type'] == 'citizen':
            c.execute('''INSERT OR IGNORE INTO party_members (party_id,official_id,official_name,applicant_type,citizen_id_number,citizen_name,joined_at)
                         VALUES (?,0,'', 'citizen', ?, ?, ?)''',
                      (r['party_id'], r['citizen_id_number'], r['citizen_name'], now))
        else:
            c.execute('''INSERT OR IGNORE INTO party_members (party_id,official_id,official_name,joined_at)
                         VALUES (?,?,?,?)''', (r['party_id'], r['official_id'], r['official_name'], now))
        c.execute("UPDATE party_requests SET status='已批准', decided_at=? WHERE id=?", (now, req_id))
        conn.commit()
        conn.close()
        return True, f'已批准 {r["official_name"] or r["citizen_name"]} 入党'

    @staticmethod
    def reject_party_request(req_id, actor_name=''):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        r = c.execute("SELECT * FROM party_requests WHERE id=?", (req_id,)).fetchone()
        if not r or r['status'] != '待审批':
            conn.close()
            return False, '申请不存在或已处理'
        now = now_str()
        c.execute("UPDATE party_requests SET status='已拒绝', decided_at=? WHERE id=?", (now, req_id))
        conn.commit()
        conn.close()
        return True, f'已拒绝 {r["official_name"] or r["citizen_name"]} 的入党申请'

    @staticmethod
    def remove_party_member(party_id, member_id, actor_id=None):
        """党魁清退党员（官员按 official_id、公民按 citizen_id_number；党魁本人不可被清退）"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        p = c.execute("SELECT leader_id FROM parties WHERE id=?", (party_id,)).fetchone()
        if actor_id is not None and p and p['leader_id'] == member_id:
            conn.close()
            return False, '党魁不能被移除，请先交接党魁'
        c.execute("DELETE FROM party_members WHERE party_id=? AND (official_id=? OR citizen_id_number=?)",
                  (party_id, member_id, member_id))
        conn.commit()
        conn.close()
        return True, '已移除该党员'

    @staticmethod
    def withdraw_candidate(election_id, candidate_id):
        """党魁撤回本党候选人（仅候选人登记阶段）"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        e = c.execute("SELECT status FROM elections WHERE id=?", (election_id,)).fetchone()
        if not e:
            conn.close()
            return False, '大选不存在'
        if e['status'] != '候选人登记':
            conn.close()
            return False, '当前阶段不允许撤回候选人'
        cand = c.execute("SELECT * FROM election_candidates WHERE id=?", (candidate_id,)).fetchone()
        if not cand or cand['election_id'] != election_id:
            conn.close()
            return False, '候选人不存在'
        c.execute("DELETE FROM election_candidates WHERE id=?", (candidate_id,))
        conn.commit()
        conn.close()
        return True, f'已撤回候选人 {cand["official_name"]}'

    # ============ V2.1 党派端：支持率 / 投票进度 ============
    @staticmethod
    def election_support_rates(election_id):
        """全体候选人得票 + 支持率（得票÷当前总有效票），按得票降序"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        cands = c.execute('''SELECT ec.*, o.department, o.rank,
            (SELECT COUNT(*) FROM election_votes ev WHERE ev.candidate_id=ec.id) votes
            FROM election_candidates ec LEFT JOIN officials o ON o.id=ec.official_id
            WHERE ec.election_id=?''', (election_id,)).fetchall()
        total = sum(x['votes'] for x in cands) or 0
        rows = []
        for x in cands:
            d = dict(x)
            d['support_rate'] = round(d['votes'] * 100.0 / total, 1) if total else 0.0
            rows.append(d)
        rows.sort(key=lambda r: r['votes'], reverse=True)
        # 投票进度：已投票人数 / 户籍总人数
        try:
            hconn = sqlite3.connect(HOUSEHOLD_DB)
            residents = hconn.execute("SELECT COUNT(*) FROM residents").fetchone()[0]
            hconn.close()
        except Exception:
            residents = 0
        voted = c.execute("SELECT COUNT(DISTINCT citizen_id_number) FROM election_votes WHERE election_id=?",
                          (election_id,)).fetchone()[0]
        conn.close()
        return {'candidates': rows, 'total_votes': total, 'voted': voted,
                'residents': residents,
                'turnout': round(voted * 100.0 / residents, 1) if residents else 0.0}

    # ================= V2：总统大选 =================
    @staticmethod
    def list_elections():
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute('''SELECT e.*,
            (SELECT COUNT(*) FROM election_candidates ec WHERE ec.election_id=e.id) candidate_cnt,
            (SELECT COUNT(*) FROM election_votes ev WHERE ev.election_id=e.id) vote_cnt
            FROM elections e ORDER BY e.id DESC''').fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def initiate_election(title, started_by, started_by_name, e_type='regular', reason=''):
        """发起大选 -> 状态：候选人登记。
        修宪生效后门禁：定期大选仅限总统任期已满/空缺；补选仅限弹劾/丧失能力/重大危机。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        now = now_str()
        # 修宪是否已生效（constitution 表有生效记录）
        constitution = c.execute("SELECT * FROM constitution WHERE status='生效中' ORDER BY version DESC LIMIT 1").fetchone()
        if constitution:
            if e_type not in ('regular', 'by'):
                conn.close()
                return False, '大选类型无效', None
            if e_type == 'regular':
                # 定期大选：无在职总统 或 现任总统任期已满
                cur = c.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
                expired = False
                if cur:
                    e_rec = c.execute("SELECT term_end FROM elections WHERE status='已结束' AND winner_id=?", (cur['id'],)).fetchone()
                    if e_rec and e_rec['term_end']:
                        expired = now_str() >= e_rec['term_end']
                if cur and not expired:
                    conn.close()
                    return False, '修宪后总统任期四年；现任总统任期未满，不得发起定期大选（仅弹劾/丧失能力/重大危机可补选）', None
            else:
                if reason not in ('弹劾定罪', '总统丧失履职能力', '国家重大危机'):
                    conn.close()
                    return False, '补选必须选择合法原因：弹劾定罪 / 总统丧失履职能力 / 国家重大危机', None
        _yr = int(now[:4])
        _dt = DB.election_dates(_yr)
        c.execute('''INSERT INTO elections (title,status,election_type,reason,started_by,started_by_name,created_at,
                     close_at,inauguration_date,term_start,term_end)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                  (title, '候选人登记', e_type, reason, started_by, started_by_name, now,
                   _dt['vote_end'], _dt['inauguration'], _dt['inauguration'],
                   '%d-01-20' % (_yr + 5)))
        eid = c.lastrowid
        conn.commit()
        conn.close()
        tip = '补选' if e_type == 'by' else '大选'
        return True, f'{tip}已发起（{e_type}），进入候选人登记阶段', eid

    @staticmethod
    def nominate_candidate(election_id, party_id, party_name, official, platform,
                           partner_id_number='', partner_name=''):
        """党派推举候选人（必须是官员）。换届制：
        ① 10-23 大选开启后、10月28日前可登记（登记期间即可投票）；
        ② 必须绑定副总统搭档（户籍核对）；
        ③ 候选人数≥2 自动开始投票。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        now = now_str()
        e = c.execute("SELECT * FROM elections WHERE id=? AND status IN ('候选人登记','投票中')",
                      (election_id,)).fetchone()
        if not e:
            conn.close()
            return False, '大选不存在或已结束（仅候选人登记/投票阶段可登记候选人）', None
        # 取候选人完整档案（id_number 用于本人/搭档校验）
        off_full = c.execute("SELECT * FROM officials WHERE id=? AND status='在职'", (official['id'],)).fetchone()
        if not off_full:
            conn.close()
            return False, '候选人不存在或不在职', None
        official = dict(off_full)
        if e['election_type'] == 'regular':
            yr = int(str(e['created_at'])[:4])
            if now > DB.candidacy_deadline_from(yr):
                conn.close()
                return False, '参选登记已截止（仅限10月28日前申请参选）', None
        # 修宪后：同一公民当选总统累计不得超过两届
        constitution = c.execute("SELECT * FROM constitution WHERE status='生效中' ORDER BY version DESC LIMIT 1").fetchone()
        if constitution:
            cnt = DB.president_term_count(official['id'])
            if cnt >= 2:
                conn.close()
                return False, f'{official["name"]} 已当选总统 {cnt} 届，依据宪法累计不得超过两届，不得再参选', None
        # 副总统搭档：身份证+姓名 → 户籍核对
        pidn = (partner_id_number or '').strip()
        pname = (partner_name or '').strip()
        if not pidn or not pname:
            conn.close()
            return False, '必须选择副总统搭档（填写搭档身份证号码与姓名）', None
        resident = DB.verify_household(pidn, pname)
        if not resident:
            conn.close()
            return False, f'副总统搭档「{pname}」在户籍系统中不存在，请核对身份证号码与姓名', None
        if resident['id_number'] == official['id_number']:
            conn.close()
            return False, '副总统搭档不能是候选人本人', None
        dup = c.execute("SELECT official_name FROM election_candidates WHERE election_id=? AND partner_id_number=?",
                        (election_id, pidn)).fetchone()
        if dup:
            conn.close()
            return False, f'{pname} 已被候选人 {dup["official_name"]} 选为副总统搭档', None
        dupc = c.execute('''SELECT official_name FROM election_candidates ec
            JOIN officials o ON o.id=ec.official_id
            WHERE ec.election_id=? AND o.id_number=?''', (election_id, pidn)).fetchone()
        if dupc:
            conn.close()
            return False, f'{pname} 已是本次大选的总统候选人，不能兼任副总统搭档', None
        try:
            c.execute('''INSERT INTO election_candidates (election_id,party_id,party_name,official_id,official_name,
                         platform,partner_id_number,partner_name,created_at)
                         VALUES (?,?,?,?,?,?,?,?,?)''',
                      (election_id, party_id, party_name, official['id'], official['name'],
                       platform, pidn, pname, now))
            cid = c.lastrowid
            conn.commit()
            conn.close()
            okv, msgv, _ = DB.auto_start_voting_if_ready(election_id)
            msg = f'候选人 {official["name"]}（搭档 {pname}）已登记' + ('，' + msgv if okv else '')
            return True, msg, cid
        except sqlite3.IntegrityError:
            conn.close()
            return False, '该官员已在本次大选中被推举', None

    @staticmethod
    def election_candidates(election_id):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute('''SELECT ec.*, o.department, o.rank,
            (SELECT COUNT(*) FROM election_votes ev WHERE ev.candidate_id=ec.id) votes
            FROM election_candidates ec LEFT JOIN officials o ON o.id=ec.official_id
            WHERE ec.election_id=? ORDER BY ec.id''', (election_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def start_voting(election_id):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        cnt = c.execute("SELECT COUNT(*) FROM election_candidates WHERE election_id=?", (election_id,)).fetchone()[0]
        if cnt < 2:
            conn.close()
            return False, '候选人不足 2 名，无法开始投票'
        c.execute("UPDATE elections SET status='投票中', voting_started_at=? WHERE id=?",
                  (now_str(), election_id))
        conn.commit()
        conn.close()
        return True, '投票已开始'

    @staticmethod
    def cast_vote(election_id, citizen_id_number, citizen_name, candidate_id):
        """公民一人一票（身份证唯一）；必须先登记居住地州。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            # 校验居住地州
            h = DB.get_conn(HOUSEHOLD_DB)
            row = h.execute("SELECT state FROM residents WHERE id_number=?",
                            (citizen_id_number,)).fetchone()
            h.close()
            if not row or not row[0]:
                conn.close()
                return False, '请先到「居住地管理」登记您所在的州，才能投票'
            c.execute('''INSERT INTO election_votes (election_id,citizen_id_number,citizen_name,candidate_id,voted_at)
                         VALUES (?,?,?,?,?)''', (election_id, citizen_id_number, citizen_name, candidate_id, now_str()))
            conn.commit()
            conn.close()
            return True, f'投票成功（计入{row[0]}）'
        except sqlite3.IntegrityError:
            conn.close()
            return False, '您已投过票，每人只有一票'

    @staticmethod
    def has_voted(election_id, citizen_id_number):
        conn = DB.get_conn(GOV_DB)
        row = conn.execute("SELECT candidate_id FROM election_votes WHERE election_id=? AND citizen_id_number=?",
                           (election_id, citizen_id_number)).fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    @staticmethod
    def tally_electoral(election_id):
        """选举人团计票：返回 (winner_dict_or_None, breakdown, electoral_totals, detail_lines)。
        - 已手动投票按真实票计
        - 每州按 60% 投票率模拟未投票公民：按该州 state_polls 支持率分配，无民调则平均分配"""
        TURNOUT = 0.60
        c = DB.get_conn(GOV_DB)
        c.row_factory = sqlite3.Row
        try:
            c.execute("ATTACH DATABASE ? AS hr", (HOUSEHOLD_DB,))
            breakdown = {st: {} for st in ELECTORAL_STATES}
            rows = c.execute('''SELECT ev.candidate_id, r.state, COUNT(*) cnt
                FROM election_votes ev
                LEFT JOIN hr.residents r ON r.id_number = ev.citizen_id_number
                WHERE ev.election_id=?
                GROUP BY ev.candidate_id, r.state''', (election_id,)).fetchall()
            cand_ids = set()
            for r in rows:
                cand_ids.add(r['candidate_id'])
                st = r['state'] if r['state'] else None
                if st:
                    breakdown.setdefault(st, {})[r['candidate_id']] = r['cnt']
            # 该大选所有候选人（含没手动拿到票的）
            all_cands = c.execute("SELECT id FROM election_candidates WHERE election_id=?",
                                  (election_id,)).fetchall()
            for r in all_cands:
                cand_ids.add(r['id'])
                for st in ELECTORAL_STATES:
                    breakdown[st].setdefault(r['id'], 0)
            electoral = {cid: 0 for cid in cand_ids}
            detail_lines = []
            for st in ELECTORAL_STATES:
                # 该州总人口
                pop = c.execute("SELECT COUNT(*) FROM hr.residents WHERE state=?", (st,)).fetchone()[0]
                expected = int(pop * TURNOUT)
                sv = breakdown.get(st, {})
                real_voted = sum(sv.values())
                phantom = max(0, expected - real_voted)
                # 按 state_polls 分配 phantom 票
                polls = {}
                try:
                    for pr in c.execute("SELECT candidate_id, support_rate FROM state_polls WHERE state=?", (st,)):
                        polls[pr['candidate_id']] = pr['support_rate']
                except Exception:
                    polls = {}
                if polls:
                    total_poll = sum(polls.get(cid, 50) for cid in cand_ids)
                    for cid in cand_ids:
                        sv[cid] = sv.get(cid, 0) + int(phantom * polls.get(cid, 50) / total_poll)
                    # 余数给支持率最高者
                    assigned = sum(int(phantom * polls.get(cid, 50) / total_poll) for cid in cand_ids)
                    leftover = phantom - assigned
                    if leftover > 0:
                        top = max(cand_ids, key=lambda x: polls.get(x, 50))
                        sv[top] += leftover
                else:
                    # 平均分配
                    base = phantom // max(1, len(cand_ids))
                    for cid in cand_ids:
                        sv[cid] = sv.get(cid, 0) + base
                    leftover = phantom - base * len(cand_ids)
                    for cid in list(cand_ids)[:leftover]:
                        sv[cid] += 1
                if not sv:
                    detail_lines.append(f"{st}（{ELECTORAL_VOTES[st]}票）：无候选人")
                    continue
                win_cid = max(sv, key=lambda x: sv[x])
                electoral[win_cid] += ELECTORAL_VOTES[st]
                detail_lines.append(
                    f"{st}（{ELECTORAL_VOTES[st]}票，人口{pop}，投票率模拟{expected}）：胜候选{win_cid} 明细 {sv}")
            cand_map = {}
            for cid in cand_ids:
                row = c.execute("SELECT * FROM election_candidates WHERE id=?", (cid,)).fetchone()
                if row:
                    cand_map[cid] = dict(row)
            winner = None
            for cid, ev in electoral.items():
                if ev >= ELECTORAL_MAJORITY:
                    winner = cand_map.get(cid)
                    break
            return winner, breakdown, electoral, detail_lines
        finally:
            c.close()

    @staticmethod
    def close_election(election_id):
        """结束大选：计票出当选总统与副总统搭档。
        regular 定期大选：当选者次年 1 月 20 日就职（前任同期卸任），计票后进入待就职；
        by 补选：立即就职交接。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        e = c.execute("SELECT * FROM elections WHERE id=?", (election_id,)).fetchone()
        if not e:
            conn.close()
            return False, '大选不存在'
        if e['status'] == '已结束':
            conn.close()
            return False, '本次大选已结束'
        cands = c.execute('''SELECT ec.*,
            (SELECT COUNT(*) FROM election_votes ev WHERE ev.candidate_id=ec.id) votes
            FROM election_candidates ec WHERE ec.election_id=?''', (election_id,)).fetchall()
        if not cands:
            conn.close()
            return False, '没有候选人，无法结束'
        # 选举人团计票
        winner, breakdown, electoral, detail_lines = DB.tally_electoral(election_id)
        if not winner:
            total_lines = '\n'.join(detail_lines)
            conn.close()
            return False, (f'无候选人获得 {ELECTORAL_MAJORITY} 张选举人票，需举行决选。\n'
                          f'当前选举人票分布：{electoral}\n{total_lines}')
        # 附加普票/选举人票数
        winner = dict(winner)
        winner_electoral = electoral.get(winner['id'], 0)
        now = now_str()
        if e['election_type'] == 'regular':
            # 定期大选：就职日=次年1月20日；任期=就职日起4年
            yr = int(str(e['created_at'])[:4])
            inauguration_date = f'{yr + 1}-01-20'
            term_start = f'{inauguration_date} 00:00:00'
            term_end = f'{yr + 5}-01-20 00:00:00'
            c.execute('''UPDATE elections SET status='已结束', ended_at=?, winner_id=?, winner_name=?,
                         winner_party=?, vp_id=0, vp_name=?, vp_id_number=?, inauguration_date=?,
                         term_start=?, term_end=?, close_at=? WHERE id=?''',
                      (now, winner['official_id'], winner['official_name'], winner['party_name'],
                       winner['partner_name'] or '', winner['partner_id_number'] or '',
                       inauguration_date, term_start, term_end, now, election_id))
            conn.commit()
            conn.close()
            try:
                DB.record_election_careers(election_id)  # 胜选/落选方自动建档并计入官员档案
            except Exception:
                pass
            detail = '\n'.join(detail_lines)
            return True, (f'{winner["official_name"]} 当选新总统（选举人票 {winner_electoral}/280，'
                          f'超过{ELECTORAL_MAJORITY}票门槛），'
                          f'副总统搭档：{winner["partner_name"] or "无"}。将于 {inauguration_date} 就职，'
                          f'前任总统同期卸任（任期 {term_start[:10]} 至 {term_end[:10]}）\n\n'
                          f'—— 各州计票明细 ——\n{detail}')
        # 补选：立即就职交接
        _ok, _msg = DB._immediate_inauguration(c, e, winner, conn)
        try:
            DB.record_election_careers(election_id)  # 补选结果同样计入官员档案
        except Exception:
            pass
        return _ok, _msg

    @staticmethod
    def _immediate_inauguration(c, e, winner, conn):
        """补选立即就职：当选者就任总统、搭档就任副总统，前任总统/副总统卸任。"""
        now = now_str()
        cur_p = c.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
        if cur_p and cur_p['id'] != winner['official_id']:
            c.execute("UPDATE officials SET status='已卸任', last_update=? WHERE id=?", (now, cur_p['id']))
            DB._log_raw(c, 'system', cur_p['id'], cur_p['name'], '总统卸任',
                        f'补选结束，{cur_p["name"]} 卸任总统')
        cur_vp = c.execute("SELECT * FROM officials WHERE position='副总统' AND status='在职'").fetchone()
        if cur_vp and cur_vp['id'] != winner['official_id'] and cur_vp['id_number'] != (winner['partner_id_number'] or ''):
            c.execute("UPDATE officials SET status='已卸任', last_update=? WHERE id=?", (now, cur_vp['id']))
        c.execute("UPDATE officials SET position='总统', department='总统府', rank='ES-1级政治任命', "
                  "status='在职', last_update=? WHERE id=?", (now, winner['official_id']))
        vp_id = 0
        vp_name = winner['partner_name'] or ''
        if winner['partner_id_number']:
            exist = c.execute("SELECT * FROM officials WHERE id_number=?", (winner['partner_id_number'],)).fetchone()
            if exist:
                c.execute("UPDATE officials SET position='副总统', department='总统府', "
                          "rank='ES-1级政治任命', status='在职', last_update=? WHERE id=?", (now, exist['id']))
                vp_id = exist['id']
            else:
                c.execute('''INSERT INTO officials (id_number,name,gender,department,position,rank,status,
                             appointed_date,last_update) VALUES (?,?,?, '总统府', '副总统', 'ES-1级政治任命',
                             '在职', ?, ?)''', (winner['partner_id_number'], vp_name, '未知', now, now))
                vp_id = c.lastrowid
        term_start = now
        term_end = (datetime.now() + timedelta(days=365 * 4)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute('''UPDATE elections SET status='已结束', ended_at=?, winner_id=?, winner_name=?, winner_party=?,
                     vp_id=?, vp_name=?, vp_id_number=?, inauguration_date=?, term_start=?, term_end=?,
                     inaugurated=1 WHERE id=?''',
                  (now, winner['official_id'], winner['official_name'], winner['party_name'],
                   vp_id, vp_name, winner['partner_id_number'] or '', now[:10], term_start, term_end, e['id']))
        DB._log_raw(c, 'system', 0, '系统', '补选就职',
                    f'补选结束，{winner["official_name"]} 就任总统（副总统 {vp_name or "无"}）')
        conn.commit()
        conn.close()
        return True, f'{winner["official_name"]} 当选总统并已就职（任期至 {term_end[:10]}），副总统：{vp_name or "无"}'

    @staticmethod
    def _career_ensure_official(c, id_number, name, gender='未知', position='', status='在职'):
        """确保官员档案存在（按身份证号匹配，已存在则不创建），返回 (official_id, created_flag)。
        供大选计档使用：胜选/落选方无档案时自动创建，有档案则复用。"""
        if not id_number and not name:
            return None, False
        exist = None
        if id_number:
            exist = c.execute("SELECT * FROM officials WHERE id_number=?", (id_number,)).fetchone()
        if not exist and name:
            exist = c.execute("SELECT * FROM officials WHERE name=? AND id_number IS NOT NULL AND id_number!=''",
                              (name,)).fetchone()
        if exist:
            return exist['id'], False
        now = now_str()
        cur = c.execute('''INSERT INTO officials (id_number,name,gender,department,position,rank,status,
                           appointed_date,last_update) VALUES (?,?,?, '', ?, '', ?, ?, ?)''',
                        (id_number or '', name or '', gender or '未知', position, status, now, now))
        return cur.lastrowid, True

    @staticmethod
    def record_election_careers(election_id):
        """大选结果计入官员档案（幂等，可重复执行）：
        胜选方（总统+副总统搭档）与落选方（候选人+搭档）均确保官员档案存在，
        并分别写入「当选/落选」履历（含当届任期）。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        e = c.execute("SELECT * FROM elections WHERE id=?", (election_id,)).fetchone()
        if not e:
            conn.close()
            return False, '大选不存在'
        if e['status'] != '已结束':
            conn.close()
            return False, '大选未结束，不能计入档案'
        cands = c.execute("SELECT * FROM election_candidates WHERE election_id=?", (election_id,)).fetchall()
        if not cands:
            conn.close()
            return False, '无候选人记录'
        now = now_str()
        term_start = (e['term_start'] or '')[:10]
        term_end = (e['term_end'] or '')[:10]
        ts_text = f'{term_start} 至 {term_end}' if term_start and term_end else '—'
        wid = e['winner_id'] or 0
        wname = e['winner_name'] or ''
        # 1) 胜选方：总统（参选须有档案，若异常缺失则按候选人信息补建）
        if wid:
            w_exist = c.execute("SELECT * FROM officials WHERE id=?", (wid,)).fetchone()
            if not w_exist:
                wc = c.execute("SELECT * FROM election_candidates WHERE election_id=? AND official_id=?",
                               (election_id, wid)).fetchone()
                w_exist = c.execute("SELECT * FROM officials WHERE name=?", (wc['official_name'],)).fetchone() if wc else None
                if w_exist:
                    c.execute("UPDATE officials SET position='总统', department='总统府', "
                              "rank='ES-1级政治任命', status='在职', last_update=? WHERE id=?", (now, wid))
            if w_exist:
                c.execute('''INSERT OR IGNORE INTO official_careers
                             (election_id, official_id, record_type, position, department,
                              term_start, term_end, note, created_at)
                             VALUES (?,?,?, '总统', '总统府', ?, ?, ?, ?)''',
                          (election_id, wid, '当选', term_start, term_end,
                           f'第 {e["id"]} 届总统大选当选总统（{e["winner_party"] or ""}），'
                           f'任期 {ts_text}，就职日 {term_start}', now))
        # 2) 胜选方：副总统搭档（无档案自动创建，职位=副总统、状态=当选待就职）
        vp_oid = None
        if e['vp_name'] or e['vp_id_number']:
            vp_oid, _vp_created = DB._career_ensure_official(
                c, e['vp_id_number'] or '', e['vp_name'] or '', gender='未知',
                position='副总统', status='当选待就职')
            if vp_oid:
                c.execute('''INSERT OR IGNORE INTO official_careers
                             (election_id, official_id, record_type, position, department,
                              term_start, term_end, note, created_at)
                             VALUES (?,?,?, '副总统', '总统府', ?, ?, ?, ?)''',
                          (election_id, vp_oid, '当选', term_start, term_end,
                           f'第 {e["id"]} 届总统大选当选副总统（{wname} 搭档），'
                           f'任期 {ts_text}，就职日 {term_start}', now))
        # 3) 落选方：候选人 + 其搭档（无档案自动创建）
        for cnd in cands:
            cid = cnd['official_id']
            if cid and cid != wid:
                ex = c.execute("SELECT * FROM officials WHERE id=?", (cid,)).fetchone()
                if ex:
                    c.execute('''INSERT OR IGNORE INTO official_careers
                                 (election_id, official_id, record_type, position, department,
                                  term_start, term_end, note, created_at)
                                 VALUES (?,?,?, '总统', ?, ?, ?, ?, ?)''',
                              (election_id, cid, '落选', ex['department'] or '', term_start, term_end,
                               f'第 {e["id"]} 届总统大选落选（{cnd["party_name"] or ""}），'
                               f'本届胜选者：{wname}', now))
            if cnd['partner_name'] or cnd['partner_id_number']:
                p_oid, _p_created = DB._career_ensure_official(
                    c, cnd['partner_id_number'] or '', cnd['partner_name'] or '',
                    gender='未知', position='', status='在职')
                if p_oid and p_oid != vp_oid:
                    c.execute('''INSERT OR IGNORE INTO official_careers
                                 (election_id, official_id, record_type, position, department,
                                  term_start, term_end, note, created_at)
                                 VALUES (?,?,?, '副总统', '', ?, ?, ?, ?)''',
                              (election_id, p_oid, '落选', term_start, term_end,
                               f'第 {e["id"]} 届总统大选落选（{cnd["official_name"]} 搭档）', now))
        conn.commit()
        conn.close()
        DB.log('system', 0, '系统', '大选计档', f'第 {e["id"]} 届总统大选结果已计入官员档案')
        return True, f'第 {e["id"]} 届总统大选：胜选方与落选方官员档案均已更新'

    @staticmethod
    def backfill_election_careers():
        """启动时补齐：对已结束但尚未计档的大选补写官员档案履历（幂等）。"""
        conn = DB.get_conn(GOV_DB)
        eids = [r[0] for r in conn.execute(
            "SELECT id FROM elections WHERE status='已结束' AND id NOT IN "
            "(SELECT DISTINCT election_id FROM official_careers WHERE election_id>0)").fetchall()]
        conn.close()
        for eid in eids:
            try:
                DB.record_election_careers(eid)
            except Exception:
                pass
        return len(eids)

    # ==================== 国库/税收/预算 ====================
    @staticmethod
    def ensure_treasury_account():
        """在银行库确保存在「蛋国国库」企业账户，返回卡号。"""
        b = DB.get_conn(BANK_DB)
        b.row_factory = sqlite3.Row
        row = b.execute("SELECT * FROM customers WHERE enterprise_name='蛋国财政部'").fetchone()
        if row:
            b.close()
            return row['card_number']
        card = '6222' + ''.join(random.choice('0123456789') for _ in range(12))
        b.execute('''INSERT INTO customers (card_number, id_number, name, password, balance,
                    credit_score, account_type, create_time, last_update_time,
                    is_enterprise, enterprise_name, unified_credit_code, legal_rep_name, legal_rep_id_number)
                    VALUES (?,?,?, 'treasury', 1000000.0, 800, '国库账户', ?, ?,
                            1, '蛋国财政部', 'DG-TREASURY-001', '财政部部长', 'TREASURY-001')''',
                 (card, 'TREASURY-001', '蛋国国库', now_str(), now_str()))
        b.commit()
        b.close()
        return card

    @staticmethod
    def treasury_balance():
        """读取国库余额。"""
        card = DB.ensure_treasury_account()
        b = DB.get_conn(BANK_DB)
        row = b.execute("SELECT balance FROM customers WHERE card_number=?", (card,)).fetchone()
        b.close()
        return row[0] if row else 0.0

    @staticmethod
    def treasury_tax_monthly():
        """每月自动收税：从有余额的公民银行账户每人扣 50 蛋元转入国库。"""
        card = DB.ensure_treasury_account()
        b = DB.get_conn(BANK_DB)
        b.row_factory = sqlite3.Row
        citizens = b.execute("SELECT * FROM customers WHERE is_enterprise=0 AND balance>=50").fetchall()
        collected = 0.0
        n = 0
        for c in citizens:
            tax = 50.0
            b.execute("UPDATE customers SET balance=balance-? WHERE card_number=?", (tax, c['card_number']))
            b.execute("UPDATE customers SET balance=balance+? WHERE card_number=?", (tax, card))
            b.execute('''INSERT INTO transactions (from_card, to_card, amount, transaction_type, transaction_time)
                         VALUES (?,?,?, '税收', ?)''', (c['card_number'], card, tax, now_str()))
            collected += tax
            n += 1
        b.commit()
        b.close()
        g = DB.get_conn(GOV_DB)
        g.execute("INSERT INTO treasury_ledger (txn_type, amount, balance_after, from_card, to_card, note, created_at) VALUES ('月度税收', ?, ?, '公民账户', ?, ?, ?)",
                  (collected, DB.treasury_balance(), card, f'从{n}位公民扣税', now_str()))
        g.commit()
        g.close()
        return n, collected

    @staticmethod
    def treasury_pay_salaries():
        """每月发薪：从国库给每位在职官员转账（按级别月薪）。"""
        card = DB.ensure_treasury_account()
        g = DB.get_conn(GOV_DB)
        g.row_factory = sqlite3.Row
        officials = g.execute("SELECT * FROM officials WHERE status='在职'").fetchall()
        b = DB.get_conn(BANK_DB)
        b.row_factory = sqlite3.Row
        paid = 0.0
        n = 0
        for o in officials:
            salary = 8000.0  # 默认月薪
            rank = (o['rank'] or '')
            if 'ES' in rank:
                salary = 50000.0
            elif 'SES' in rank:
                salary = 30000.0
            elif 'GS' in rank:
                salary = 15000.0
            cust = b.execute("SELECT * FROM customers WHERE id_number=?", (o['id_number'],)).fetchone()
            if not cust:
                continue
            bal = b.execute("SELECT balance FROM customers WHERE card_number=?", (card,)).fetchone()[0]
            if bal < salary:
                continue
            b.execute("UPDATE customers SET balance=balance-? WHERE card_number=?", (salary, card))
            b.execute("UPDATE customers SET balance=balance+? WHERE card_number=?", (salary, cust['card_number']))
            b.execute('''INSERT INTO transactions (from_card, to_card, amount, transaction_type, transaction_time)
                         VALUES (?,?,?, '公务员薪资', ?)''', (card, cust['card_number'], salary, now_str()))
            paid += salary
            n += 1
        b.commit()
        b.close()
        g.execute("INSERT INTO treasury_ledger (txn_type, amount, balance_after, from_card, to_card, note, created_at) VALUES ('薪资发放', ?, ?, ?, ?, ?, ?)",
                  (-paid, DB.treasury_balance(), card, '官员账户', f'{n}位在职官员发薪', now_str()))
        g.commit()
        g.close()
        return n, paid

    @staticmethod
    def treasury_log():
        g = DB.get_conn(GOV_DB)
        rows = g.execute("SELECT * FROM treasury_ledger ORDER BY id DESC LIMIT 50").fetchall()
        g.close()
        return rows

    @staticmethod
    def auto_monthly_treasury():
        """每月 1 号自动收税+发薪（每 5 分钟 tick 一次，幂等）。
        通过查 treasury_ledger 最近一条'月度税收'的年月判断本月是否已跑。"""
        g = DB.get_conn(GOV_DB)
        row = g.execute("SELECT created_at FROM treasury_ledger WHERE txn_type='月度税收' ORDER BY id DESC LIMIT 1").fetchone()
        g.close()
        today = datetime.now().strftime('%Y-%m')
        if row and row[0] and row[0][:7] == today:
            return False, '本月税收已自动完成'
        try:
            n_tax, amt_tax = DB.treasury_tax_monthly()
            n_sal, amt_sal = DB.treasury_pay_salaries()
            return True, f'本月国库自动结算：收税 ¥{amt_tax:,.2f}（{n_tax}人），发薪 ¥{amt_sal:,.2f}（{n_sal}人）'
        except Exception as e:
            return False, f'国库自动结算失败: {e}'

    # ==================== 司法违宪审查 ====================
    @staticmethod
    def file_constitutional_case(target_type, target_id, plaintiff, plaintiff_id_number, reason):
        """提起违宪诉讼。target_type: 'executive_order' / 'law'。"""
        g = DB.get_conn(GOV_DB)
        g.row_factory = sqlite3.Row
        case_no = f'CC-{datetime.now().strftime("%Y%m%d")}-{random.randint(1000,9999)}'
        g.execute('''INSERT INTO constitutional_cases (case_no, target_type, target_id, plaintiff,
                    plaintiff_id_number, reason, filed_at) VALUES (?,?,?,?,?,?,?)''',
                 (case_no, target_type, target_id, plaintiff, plaintiff_id_number, reason, now_str()))
        g.commit()
        cid = g.execute("SELECT last_insert_rowid()").fetchone()[0]
        g.close()
        return cid, case_no

    @staticmethod
    def rule_constitutional_case(case_id, ruling, ruling_note, justice_id=0):
        """最高法院裁定。ruling: '违宪' / '合宪'。违宪则自动废止目标。"""
        g = DB.get_conn(GOV_DB)
        g.row_factory = sqlite3.Row
        case = g.execute("SELECT * FROM constitutional_cases WHERE id=?", (case_id,)).fetchone()
        if not case:
            g.close()
            return False, '案件不存在'
        g.execute("UPDATE constitutional_cases SET status='已裁定', ruling=?, ruling_note=?, ruled_at=?, justice_id=? WHERE id=?",
                  (ruling, ruling_note, now_str(), justice_id, case_id))
        if ruling == '违宪':
            if case['target_type'] == 'executive_order':
                try:
                    g.execute("UPDATE executive_orders SET is_withdrawn=1, withdrawn_at=? WHERE id=?",
                              (now_str(), case['target_id']))
                except Exception:
                    g.execute("UPDATE executive_orders SET status='已废止' WHERE id=?", (case['target_id'],))
                note = f'行政令 #{case["target_id"]} 因违宪被废止'
            elif case['target_type'] == 'law':
                g.execute("UPDATE laws SET status='已冻结' WHERE id=?", (case['target_id'],))
                note = f'法律 #{case["target_id"]} 因违宪被冻结'
            else:
                note = '无对应目标'
        else:
            note = '裁定合宪，继续有效'
        g.commit()
        g.close()
        return True, f'案件 {case["case_no"]} 裁定：{ruling}。{note}'

    @staticmethod
    def list_constitutional_cases(status=None):
        g = DB.get_conn(GOV_DB)
        if status:
            rows = g.execute("SELECT * FROM constitutional_cases WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = g.execute("SELECT * FROM constitutional_cases ORDER BY id DESC").fetchall()
        g.close()
        return rows

    # ==================== 媒体事件引擎 + 动态民调 ====================
    NEWS_POOL = [
        ('就业数据向好', '上月新增就业岗位同比增长 3.2%，失业率下降 0.4 个百分点。', '经济', '正面', 2.0, '全国'),
        ('股市大跌', '蛋国综指本周累计下跌 5.8%，投资者信心受挫。', '经济', '负面', -2.5, '全国'),
        ('总统支持率上升', '最新民调显示总统施政满意度上升 3 个百分点。', '政治', '正面', 1.5, '全国'),
        ('反腐风暴', '财政部一名司长因涉嫌贪腐被立案调查。', '政治', '负面', -1.8, '全国'),
        ('外交突破', '蛋国与邻国签署自由贸易协定，双边贸易额预计增长。', '外交', '正面', 2.2, '全国'),
        ('天灾预警', '东部沿海发布台风红色预警，沿海各州进入防灾状态。', '灾害', '负面', -2.0, '海文州'),
        ('教育改革落地', '全国中小学新学期启用新教材，家长反应积极。', '社会', '正面', 1.0, '全国'),
        ('油价上涨', '国际油价连续三周上涨，国内成品油价格上调。', '经济', '负面', -1.5, '全国'),
        ('奥运摘金', '蛋国代表团在奥运会上斩获 3 枚金牌，举国欢庆。', '体育', '正面', 2.5, '全国'),
        ('议员丑闻', '一名联邦参议员被曝出酒后失德视频，反对党呼吁辞职。', '政治', '负面', -2.2, '全国'),
        ('医保扩面', '全民医保覆盖范围扩大至新就业形态劳动者。', '社会', '正面', 1.8, '全国'),
        ('洪水预警', '南部米伦州遭遇暴雨，部分城镇内涝。', '灾害', '负面', -2.3, '米伦州'),
    ]

    @staticmethod
    def generate_weekly_news():
        """随机生成 1 条本周新闻事件，并按影响调整各州民调。"""
        g = DB.get_conn(GOV_DB)
        g.row_factory = sqlite3.Row
        title, content, cat, direction, impact, state = random.choice(DB.NEWS_POOL)
        g.execute('''INSERT INTO news_events (title, content, category, impact_direction, impact_value, affected_state, created_at)
                     VALUES (?,?,?,?,?,?,?)''', (title, content, cat, direction, impact, state, now_str()))
        g.commit()
        eid = g.execute("SELECT last_insert_rowid()").fetchone()[0]
        # 调整民调：impact 作用于全国或指定州的所有候选人支持率（相对调整）
        # 简单实现：全国事件对所有州调整；州事件只对该州调整
        target_states = ELECTORAL_STATES if state == '全国' else [state]
        for st in target_states:
            rows = g.execute("SELECT candidate_id, support_rate FROM state_polls WHERE state=?", (st,)).fetchall()
            for r in rows:
                new_rate = max(10, min(90, r['support_rate'] + impact * random.uniform(0.5, 1.5)))
                g.execute("UPDATE state_polls SET support_rate=?, updated_at=? WHERE state=? AND candidate_id=?",
                          (new_rate, now_str(), st, r['candidate_id']))
        g.commit()
        g.close()
        return eid, title

    @staticmethod
    def ensure_state_polls_for_election(election_id):
        """大选开启时，为每个候选人在每州初始化 50% 支持率。"""
        g = DB.get_conn(GOV_DB)
        g.row_factory = sqlite3.Row
        cands = g.execute("SELECT id FROM election_candidates WHERE election_id=?", (election_id,)).fetchall()
        for c in cands:
            for st in ELECTORAL_STATES:
                g.execute('''INSERT OR IGNORE INTO state_polls (state, candidate_id, support_rate, updated_at)
                             VALUES (?,?,?,?)''', (st, c['id'], 50.0, now_str()))
        g.commit()
        g.close()

    @staticmethod
    def list_news(limit=20):
        g = DB.get_conn(GOV_DB)
        rows = g.execute("SELECT * FROM news_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        g.close()
        return rows

    # ==================== 公民请愿 / 公投 ====================
    @staticmethod
    def create_petition(title, description, sponsor_id_number, sponsor_name, signatures_needed=500):
        g = DB.get_conn(GOV_DB)
        g.execute('''INSERT INTO petitions (title, description, sponsor_id_number, sponsor_name,
                    signatures_needed, created_at) VALUES (?,?,?,?,?,?)''',
                 (title, description, sponsor_id_number, sponsor_name, signatures_needed, now_str()))
        g.commit()
        pid = g.execute("SELECT last_insert_rowid()").fetchone()[0]
        g.close()
        return pid

    @staticmethod
    def sign_petition(petition_id, citizen_id_number, citizen_name):
        g = DB.get_conn(GOV_DB)
        try:
            g.execute("INSERT INTO petition_signatures (petition_id, citizen_id_number, citizen_name, signed_at) VALUES (?,?,?,?)",
                      (petition_id, citizen_id_number, citizen_name, now_str()))
            g.execute("UPDATE petitions SET signatures_count=signatures_count+1 WHERE id=?", (petition_id,))
            g.commit()
            # 检查是否达到阈值
            row = g.execute("SELECT signatures_count, signatures_needed, status, title FROM petitions WHERE id=?",
                            (petition_id,)).fetchone()
            if row and row[0] >= row[1] and row[2] == '收集签名':
                # 转公投
                g.execute("UPDATE petitions SET status='已转公投' WHERE id=?", (petition_id,))
                g.execute('''INSERT INTO referenda (petition_id, title, description, status, created_at)
                             VALUES (?,?,?, '投票中', ?)''',
                          (petition_id, row[3], '', now_str()))
                g.commit()
            g.close()
            return True, '签名成功'
        except sqlite3.IntegrityError:
            g.close()
            return False, '您已签过这份请愿'

    @staticmethod
    def vote_referendum(referendum_id, citizen_id_number, citizen_name, choice):
        g = DB.get_conn(GOV_DB)
        try:
            g.execute("INSERT INTO referendum_votes (referendum_id, citizen_id_number, citizen_name, choice, voted_at) VALUES (?,?,?,?,?)",
                      (referendum_id, citizen_id_number, citizen_name, choice, now_str()))
            if choice == '赞成':
                g.execute("UPDATE referenda SET yes_votes=yes_votes+1 WHERE id=?", (referendum_id,))
            else:
                g.execute("UPDATE referenda SET no_votes=no_votes+1 WHERE id=?", (referendum_id,))
            g.commit()
            g.close()
            return True, '投票成功'
        except sqlite3.IntegrityError:
            g.close()
            return False, '您已投过这份公投'

    @staticmethod
    def close_referendum(referendum_id):
        """结束公投，按 50% 阈值判定结果。"""
        g = DB.get_conn(GOV_DB)
        g.row_factory = sqlite3.Row
        r = g.execute("SELECT * FROM referenda WHERE id=?", (referendum_id,)).fetchone()
        if not r:
            g.close()
            return False, '公投不存在'
        total = r['yes_votes'] + r['no_votes']
        if total == 0:
            g.close()
            return False, '无投票'
        yes_ratio = r['yes_votes'] / total
        result = '通过' if yes_ratio >= r['threshold'] else '未通过'
        g.execute("UPDATE referenda SET status='已结束', result=?, closed_at=? WHERE id=?",
                  (result, now_str(), referendum_id))
        g.commit()
        g.close()
        return True, f'公投「{r["title"]}」{result}（赞成{r["yes_votes"]}/反对{r["no_votes"]}，通过率{yes_ratio:.1%}）'

    @staticmethod
    def list_petitions(status=None):
        g = DB.get_conn(GOV_DB)
        if status:
            rows = g.execute("SELECT * FROM petitions WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = g.execute("SELECT * FROM petitions ORDER BY id DESC").fetchall()
        g.close()
        return rows

    @staticmethod
    def list_referenda(status=None):
        g = DB.get_conn(GOV_DB)
        if status:
            rows = g.execute("SELECT * FROM referenda WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = g.execute("SELECT * FROM referenda ORDER BY id DESC").fetchall()
        g.close()
        return rows



    @staticmethod
    def official_careers(official_id):
        """某官员的完整档案履历（历任职务/任期 + 当选/落选记录），按时间正序。"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM official_careers WHERE official_id=? ORDER BY id ASC",
                            (official_id,)).fetchall()
        conn.close()
        out = []
        for r in rows:
            ts, te = (r['term_start'] or '')[:10], (r['term_end'] or '')[:10]
            out.append({'id': r['id'], 'election_id': r['election_id'] or 0,
                        'record_type': r['record_type'], 'position': r['position'] or '—',
                        'department': r['department'] or '',
                        'term_start': ts, 'term_end': te,
                        'term_text': f'{ts} 至 {te}' if ts and te else '—',
                        'note': r['note'] or '', 'created_at': r['created_at'] or ''})
        return out

    @staticmethod
    def auto_close_election():
        """11月3日自动结束并计票本年定期大选（幂等：已结束跳过）。"""
        dt = datetime.now()
        if not (dt.month == 11 and dt.day == 3 and (dt.year - 2026) % 4 == 0):
            return False, '未到计票日（11月3日）', None
        conn = DB.get_conn(GOV_DB)
        e = conn.execute("SELECT * FROM elections WHERE election_type='regular' AND status IN ('候选人登记','投票中') "
                         "AND created_at>=? ORDER BY id DESC LIMIT 1", (f'{dt.year}-10-23',)).fetchone()
        conn.close()
        if not e:
            return False, '本年没有待计票的定期大选', None
        ok, msg = DB.close_election(e['id'])
        if ok:
            DB.log('system', 0, '系统', '自动计票', f'11月3日自动计票：{msg}')
        return ok, msg, e['id']

    @staticmethod
    def auto_inauguration():
        """次年1月20日自动就职交接：当选总统+副总统搭档上任。
        连任（当选者=现任总统）：总统状态/职位不变，仅更新任期记录；
        未连任：前任总统职位切换为「总统府的总统卸任待定」，副总统卸任。"""
        dt = datetime.now()
        if not (dt.month == 1 and dt.day == 20):
            return False, '未到就职日（1月20日）', None
        today = f'{dt.year}-01-20'
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        e = c.execute('''SELECT * FROM elections WHERE election_type='regular' AND status='已结束'
                         AND inaugurated=0 AND winner_id>0 AND inauguration_date<=?
                         ORDER BY id DESC LIMIT 1''', (today,)).fetchone()
        if not e:
            conn.close()
            return False, '没有待就职的当选总统', None
        now = now_str()
        cur_p = c.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
        is_re_elected = bool(cur_p and cur_p['id'] == e['winner_id'])
        if cur_p:
            if is_re_elected:
                # 连任：总统状态、职位不变（仅届数/任期记录更新）
                DB._log_raw(c, 'system', cur_p['id'], cur_p['name'], '总统连任',
                            f'{e["winner_name"]} 连任总统（任期至 {dt.year + 4}-01-20）')
            else:
                # 未连任：前任总统切换为总统府的「总统卸任待定」
                c.execute("UPDATE officials SET position='总统卸任待定', department='总统府', "
                          "status='在职', last_update=? WHERE id=?", (now, cur_p['id']))
                DB._log_raw(c, 'system', cur_p['id'], cur_p['name'], '总统卸任待定',
                            f'{e["winner_name"]} 就职，{cur_p["name"]} 转为总统府·总统卸任待定')
        # 旧副总统卸任（非当选者本人、非新搭档）
        cur_vp = c.execute("SELECT * FROM officials WHERE position='副总统' AND status='在职'").fetchone()
        if cur_vp and cur_vp['id'] != e['winner_id'] and cur_vp['id_number'] != (e['vp_id_number'] or ''):
            c.execute("UPDATE officials SET status='已卸任', last_update=? WHERE id=?", (now, cur_vp['id']))
        # 当选者就任总统（连任时同值更新，状态/职位不变）
        c.execute("UPDATE officials SET position='总统', department='总统府', rank='ES-1级政治任命', "
                  "status='在职', last_update=? WHERE id=?", (now, e['winner_id']))
        # 搭档就任副总统
        vp_id = 0
        if e['vp_id_number'] and e['vp_name']:
            exist = c.execute("SELECT * FROM officials WHERE id_number=?", (e['vp_id_number'],)).fetchone()
            if exist:
                c.execute("UPDATE officials SET position='副总统', department='总统府', "
                          "rank='ES-1级政治任命', status='在职', last_update=? WHERE id=?", (now, exist['id']))
                vp_id = exist['id']
            else:
                c.execute('''INSERT INTO officials (id_number,name,gender,department,position,rank,status,
                             appointed_date,last_update) VALUES (?,?,?, '总统府', '副总统', 'ES-1级政治任命',
                             '在职', ?, ?)''', (e['vp_id_number'], e['vp_name'], '未知', now, now))
                vp_id = c.lastrowid
        c.execute('''UPDATE elections SET inaugurated=1, vp_id=?, inaugurated_at=?, term_start=?, term_end=?
                     WHERE id=?''', (vp_id, now, f'{e["inauguration_date"]} 00:00:00',
                                     f'{dt.year + 4}-01-20 00:00:00', e['id']))
        conn.commit()
        conn.close()
        DB.log('system', 0, '系统', '总统就职',
               f'{e["winner_name"]} 于 {today} 就职总统（副总统 {e["vp_name"] or "无"} 同日就任）')
        return True, f'{e["winner_name"]} 今日就职总统，副总统 {e["vp_name"] or "无"} 同日就任（任期至 {dt.year + 4}-01-20）', e['id']

    @staticmethod
    def auto_start_voting_if_ready(election_id=None):
        """候选人数≥2 且仍处候选人登记 → 自动开始投票（供推举/审核通过后调用）。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        if election_id:
            eids = [election_id]
        else:
            eids = [r[0] for r in c.execute(
                "SELECT id FROM elections WHERE status='候选人登记' AND election_type='regular'").fetchall()]
        ok_any = False
        msgs = []
        ret_id = None
        for eid in eids:
            cnt = c.execute("SELECT COUNT(*) FROM election_candidates WHERE election_id=?", (eid,)).fetchone()[0]
            if cnt >= 2:
                c.execute("UPDATE elections SET status='投票中', voting_started_at=? "
                          "WHERE id=? AND status='候选人登记'", (now_str(), eid))
                if c.rowcount:
                    ok_any = True
                    ret_id = eid
                    msgs.append(f'候选人数已达 2 人，大选自动进入投票阶段')
        conn.commit()
        conn.close()
        if ok_any:
            return True, '；'.join(msgs), ret_id
        return False, '候选人数不足 2 人，暂未自动开票', None

    @staticmethod
    def clear_presidential_history():
        """清空所有总统档案与大选记录（elections/candidates/votes/参选申请），任职届数清零。
        保留在职总统/副总统本人档案（职务不变）；删除纯竞选登记（总统候选人）档案。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        c.execute("DELETE FROM election_votes")
        c.execute("DELETE FROM election_candidates")
        c.execute("DELETE FROM elections")
        c.execute("DELETE FROM candidacy_applications")
        c.execute("DELETE FROM officials WHERE position='总统候选人' AND department='竞选登记'")
        conn.commit()
        conn.close()
        DB.log('admin', 0, '系统', '清空总统档案', '清空全部大选记录与竞选登记档案，任职届数清零')
        return True, '已清空全部总统档案与大选记录，任职届数清零'

    @staticmethod
    def _log_raw(c, otype, oid, oname, action, details):
        try:
            c.execute("INSERT INTO system_logs (operator_type,operator_id,operator_name,action,details,created_at) "
                      "VALUES (?,?,?,?,?,?)", (otype, str(oid), oname, action, details, now_str()))
        except Exception:
            pass

    # ================= 修宪模块 =================
    @staticmethod
    def current_constitution():
        """当前生效宪法（无则 None）。"""
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT * FROM constitution WHERE status='生效中' ORDER BY version DESC LIMIT 1").fetchone()
        conn.close()
        return dict(r) if r else None

    @staticmethod
    def submit_amendment(title, content, proposer_id, proposer_name):
        """总统提交修宪草案。content 每条修正案一行。"""
        if not title or not content:
            return False, '请填写修正案标题与条文', None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        now = now_str()
        # 校验发起人为现任总统
        pres = c.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
        if not pres or pres['id'] != int(proposer_id or 0):
            conn.close()
            return False, '只有现任总统才能提交修宪草案', None
        # 存在待审议/审议中的草案时不允许重复提交
        pending = c.execute("SELECT COUNT(*) FROM constitutional_amendments WHERE status IN ('待审议','审议中')").fetchone()[0]
        if pending:
            conn.close()
            return False, '已有修宪草案正在审议中，请等待审议结果', None
        c.execute('''INSERT INTO constitutional_amendments (title,content,proposer_id,proposer_name,status,created_at)
                     VALUES (?,?,?,?, '待审议', ?)''', (title, content, proposer_id, proposer_name, now))
        aid = c.lastrowid
        conn.commit()
        conn.close()
        return True, f'修宪草案《{title}》已提交，等待议会审议', aid

    @staticmethod
    def list_amendments(status=None):
        conn = DB.get_conn(GOV_DB)
        if status:
            rows = conn.execute("SELECT * FROM constitutional_amendments WHERE status=? ORDER BY id DESC",
                                (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM constitutional_amendments ORDER BY id DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def is_legislator(official):
        """是否在职议员（参议员/众议员/议长）。"""
        return bool(official) and official.get('position') in ('参议员', '众议员', '参议院议长', '众议院议长') \
            and official.get('status') == '在职'

    @staticmethod
    def cast_amendment_vote(amendment_id, official_id, official_name, vote):
        """议员对修宪草案投票（一人一票）。"""
        if vote not in ('赞成', '反对', '弃权'):
            return False, '投票选项无效'
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        a = c.execute("SELECT * FROM constitutional_amendments WHERE id=?", (amendment_id,)).fetchone()
        if not a:
            conn.close()
            return False, '草案不存在'
        if a['status'] not in ('待审议', '审议中'):
            conn.close()
            return False, '该草案已结束审议'
        off = c.execute("SELECT * FROM officials WHERE id=? AND status='在职'", (official_id,)).fetchone()
        if not off or off['position'] not in ('参议员', '众议员', '参议院议长', '众议院议长'):
            conn.close()
            return False, '只有在职议员可以参与修宪投票'
        if a['status'] == '待审议':
            c.execute("UPDATE constitutional_amendments SET status='审议中' WHERE id=?", (amendment_id,))
        try:
            c.execute('''INSERT INTO amendment_votes (amendment_id,official_id,official_name,vote,voted_at)
                         VALUES (?,?,?,?,?)''', (amendment_id, official_id, official_name, vote, now_str()))
            conn.commit()
            conn.close()
            return True, '投票成功'
        except sqlite3.IntegrityError:
            conn.close()
            return False, '您已投过票，每位议员一票'

    @staticmethod
    def amendment_stats(amendment_id):
        conn = DB.get_conn(GOV_DB)
        a = c = None
        try:
            conn = DB.get_conn(GOV_DB)
            c = conn.cursor()
            a = c.execute("SELECT * FROM constitutional_amendments WHERE id=?", (amendment_id,)).fetchone()
            agg = {r[0]: r[1] for r in c.execute(
                "SELECT vote,COUNT(*) FROM amendment_votes WHERE amendment_id=? GROUP BY vote", (amendment_id,)).fetchall()}
            voters = c.execute('''SELECT av.*, o.department, o.position FROM amendment_votes av
                LEFT JOIN officials o ON o.id=av.official_id WHERE av.amendment_id=? ORDER BY av.id''',
                               (amendment_id,)).fetchall()
            conn.close()
            return {'amendment': dict(a), 'yes': agg.get('赞成', 0), 'no': agg.get('反对', 0),
                    'abstain': agg.get('弃权', 0), 'voters': [dict(v) for v in voters]}
        except Exception:
            if conn:
                conn.close()
            return {'amendment': dict(a) if a else {}, 'yes': 0, 'no': 0, 'abstain': 0, 'voters': []}

    @staticmethod
    def close_amendment(amendment_id):
        """结束审议：赞成 > 反对 → 修正案通过并自动生效（写入宪法档案）；否则否决。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        a = c.execute("SELECT * FROM constitutional_amendments WHERE id=?", (amendment_id,)).fetchone()
        if not a:
            conn.close()
            return False, '草案不存在'
        if a['status'] not in ('待审议', '审议中'):
            conn.close()
            return False, '该草案已结束审议'
        agg = {r[0]: r[1] for r in c.execute(
            "SELECT vote,COUNT(*) FROM amendment_votes WHERE amendment_id=? GROUP BY vote", (amendment_id,)).fetchall()}
        yes, no = agg.get('赞成', 0), agg.get('反对', 0)
        abstain = agg.get('弃权', 0)
        now = now_str()
        if yes > no:
            # 通过：写入宪法档案（版本+1）
            ver = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM constitution").fetchone()[0]
            c.execute('''INSERT INTO constitution (version,title,items,status,passed_at,updated_at)
                         VALUES (?,?,?, '生效中', ?, ?)''',
                      (ver, a['title'], a['content'], now, now))
            # 修宪生效：为现任总统补齐任期（其当选记录无任期时，自修宪生效日起算四年）
            cur_p = c.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
            if cur_p:
                e_rec = c.execute("SELECT id,term_end FROM elections WHERE status='已结束' AND winner_id=?",
                                  (cur_p['id'],)).fetchone()
                if e_rec and not e_rec['term_end']:
                    te = (datetime.now() + timedelta(days=365 * 4)).strftime('%Y-%m-%d %H:%M:%S')
                    c.execute("UPDATE elections SET term_start=?, term_end=? WHERE id=?", (now, te, e_rec['id']))
            c.execute("UPDATE constitutional_amendments SET status='已通过', reviewed_at=?, "
                      "yes_count=?, no_count=?, abstain_count=? WHERE id=?",
                      (now, yes, no, abstain, amendment_id))
            conn.commit()
            conn.close()
            return True, f'修正案《{a["title"]}》审议通过并自动生效！赞成 {yes} / 反对 {no} / 弃权 {abstain}'
        else:
            c.execute("UPDATE constitutional_amendments SET status='已否决', reviewed_at=?, "
                      "yes_count=?, no_count=?, abstain_count=? WHERE id=?",
                      (now, yes, no, abstain, amendment_id))
            conn.commit()
            conn.close()
            return False, f'修正案《{a["title"]}》未获通过（赞成 {yes} / 反对 {no} / 弃权 {abstain}）'

    @staticmethod
    def remove_amendment_vote(amendment_id, official_id):
        """管理员撤销某议员的投票（改票用）。"""
        conn = DB.get_conn(GOV_DB)
        conn.execute("DELETE FROM amendment_votes WHERE amendment_id=? AND official_id=?", (amendment_id, official_id))
        conn.commit()
        conn.close()

    # ================= 弹劾与总统自动继任 =================
    @staticmethod
    def _auto_succession(c, reason, action, pres_term_end=None):
        """底层宪法继任规则：总统被撤职/删除（非大选落选）时，在职副总统自动当选总统。
        继任总统沿用原总统剩余任期（pres_term_end，如原总统本该 2028 年卸任则 2028 年卸任），
        原总统无任期记录时按四年新任期。返回接任总统姓名；无副总统返回 None（等待补选）。"""
        vp = c.execute("SELECT * FROM officials WHERE position='副总统' AND status='在职' ORDER BY id LIMIT 1").fetchone()
        if not vp:
            DB._log_raw(c, 'system', 0, '系统', '总统职位空缺',
                        f'{reason}，但无在职副总统可接任，等待大选/补选')
            return None
        now = now_str()
        c.execute("UPDATE officials SET position='总统', department='总统府', rank='ES-1级政治任命', "
                  "status='在职', last_update=? WHERE id=?", (now, vp['id']))
        term_start = now
        term_end = pres_term_end or (datetime.now() + timedelta(days=365 * 4)).strftime('%Y-%m-%d %H:%M:%S')
        party = c.execute("SELECT p.name FROM party_members pm JOIN parties p ON p.id=pm.party_id "
                          "WHERE pm.official_id=? LIMIT 1", (vp['id'],)).fetchone()
        vp_party = party['name'] if party else ''
        c.execute('''INSERT INTO elections (title,status,election_type,reason,term_start,term_end,
                     started_by,started_by_name,winner_id,winner_name,winner_party,created_at,ended_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  ('总统交接', '已结束', 'by', f'{reason}·副总统依宪继任', term_start, term_end,
                   'system', '宪法继任规则', vp['id'], vp['name'], vp_party, now, now))
        DB._log_raw(c, 'system', vp['id'], vp['name'], '总统自动继任',
                    f'{reason}，副总统{vp["name"]}自动当选总统（沿用原总统任期：{term_start[:10]} 至 {term_end[:10]}）')
        return vp['name']

    @staticmethod
    def president_term_count(official_id):
        """某公民当选总统的届数（依据 elections 已结束记录的 winner_id）"""
        conn = DB.get_conn(GOV_DB)
        cnt = conn.execute("SELECT COUNT(*) FROM elections WHERE status='已结束' AND winner_id=?",
                           (official_id,)).fetchone()[0]
        conn.close()
        return cnt

    # ================= 任期管理 / 任职协议 / 连任 / 合同（新需求） =================
    @staticmethod
    def manageable_officials(manager, keyword=''):
        """上级可管理的官员列表（不含自己）：总统/副总统→全部在职官员；
        部长/局长/署长/处长→本部门在职官员；其余返回空列表。
        keyword 可模糊匹配 姓名/身份证号/职务/部门。"""
        pos = (manager.get('position') or '').strip()
        conn = DB.get_conn(GOV_DB)
        rows = []
        try:
            if pos in ('总统', '副总统'):
                sql, p = "SELECT * FROM officials WHERE status='在职'", []
            elif '部长' in pos or '局长' in pos or '署长' in pos or '处长' in pos:
                sql, p = ("SELECT * FROM officials WHERE status='在职' AND department=?",
                          [manager.get('department') or ''])
            else:
                sql = None
            if sql is not None:
                kw = (keyword or '').strip()
                if kw:
                    sql += " AND (name LIKE ? OR id_number LIKE ? OR position LIKE ? OR department LIKE ?)"
                    p += [f'%{kw}%'] * 4
                sql += " ORDER BY id"
                rows = conn.execute(sql, p).fetchall()
        finally:
            conn.close()
        me = manager.get('id')
        return [dict(r) for r in rows if r['id'] != me]

    @staticmethod
    def tenure_scope_label(manager):
        pos = (manager.get('position') or '').strip()
        if pos in ('总统', '副总统'):
            return '全部官员'
        if '部长' in pos or '局长' in pos or '署长' in pos or '处长' in pos:
            return f'本部门（{manager.get("department") or ""}）'
        return '无权管理'

    @staticmethod
    def _add_career(c, official_id, record_type, position, department,
                    term_start, term_end, note=''):
        """写入一条非竞选履历（election_id=0），供任命/调整/连任/卸任共用。"""
        c.execute('''INSERT INTO official_careers (election_id,official_id,record_type,position,department,
                     term_start,term_end,note,created_at) VALUES (0,?,?,?,?,?,?,?,?)''',
                  (official_id, record_type, position or '', department or '',
                   term_start or '', term_end or '', note, now_str()))

    @staticmethod
    def _new_agreement(c, official_id, position, department, term_start, term_end):
        """为一次任职写入待签署协议（agreed=0）；每次任命/调整职务/调整任期都会生成新协议。"""
        c.execute('''INSERT INTO career_agreements (official_id,position,department,term_start,term_end,
                     agreed,created_at) VALUES (?,?,?,?,?,0,?)''',
                  (official_id, position or '', department or '', term_start or '', term_end or '', now_str()))

    @staticmethod
    def pending_agreement(official_id):
        """返回该官员最新一条未签署的任职协议（dict 或 None）。"""
        conn = DB.get_conn(GOV_DB)
        try:
            r = conn.execute("SELECT * FROM career_agreements WHERE official_id=? AND agreed=0 "
                             "ORDER BY id DESC LIMIT 1", (official_id,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    @staticmethod
    def sign_agreement(agreement_id, official_id):
        """官员签署任职协议；签署后返回 True。"""
        conn = DB.get_conn(GOV_DB)
        try:
            r = conn.execute("SELECT * FROM career_agreements WHERE id=? AND official_id=? AND agreed=0",
                             (agreement_id, official_id)).fetchone()
            if not r:
                return False, '协议不存在或已签署'
            conn.execute("UPDATE career_agreements SET agreed=1, agreed_at=? WHERE id=?",
                         (now_str(), agreement_id))
            conn.commit()
            return True, '已签署任职协议'
        finally:
            conn.close()

    @staticmethod
    def ensure_presidential_agreement(official_id):
        """总统/副总统首次登录时补建一条欢迎协议。
        关键：只在该官员从未签过任何协议时才建，避免每次登录重复弹。"""
        conn = DB.get_conn(GOV_DB)
        try:
            # 已有任何协议记录（无论 agreed=0/1）→ 不重建
            existed = conn.execute(
                "SELECT 1 FROM career_agreements WHERE official_id=? LIMIT 1",
                (official_id,)).fetchone()
            if existed:
                return False
            off = conn.execute("SELECT * FROM officials WHERE id=?", (official_id,)).fetchone()
            if not off:
                return False
            ts = off['term_start'] or ''
            te = off['term_end'] or ''
            if not ts or not te:
                e = conn.execute(
                    "SELECT term_start, term_end FROM elections WHERE status='已结束' "
                    "AND (winner_id=? OR vp_id=?) ORDER BY id DESC LIMIT 1",
                    (official_id, official_id)).fetchone()
                if e:
                    ts = ts or (e['term_start'] or '')[:10]
                    te = te or (e['term_end'] or '')[:10]
            if not ts:
                ts = datetime.now().strftime('%Y-%m-%d')
            if not te:
                te = (datetime.now() + timedelta(days=4 * 365)).strftime('%Y-%m-%d')
            DB._new_agreement(conn, official_id, off['position'], off['department'], ts, te)
            conn.commit()
            return True
        finally:
            conn.close()

    # ================= 法律发布 =================
    @staticmethod
    def propose_law(title, content, vote_deadline, effective_date, proposer_id, proposer_name, amend_of=0):
        """总统起草法律（或修法）→ 进入议员投票阶段。"""
        if not title.strip():
            return False, '请填写法律标题'
        if not content.strip():
            return False, '请填写法条正文'
        if not vote_deadline:
            return False, '请选择最后审批时间'
        if not effective_date:
            return False, '请选择生效日期'
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute('''INSERT INTO laws (title, content, proposer_id, proposer_name,
                             vote_deadline, effective_date, is_amendment_of, created_at)
                            VALUES (?,?,?,?,?,?,?,?)''',
                         (title.strip(), content, proposer_id, proposer_name,
                          vote_deadline, effective_date, amend_of or 0, now_str()))
            conn.commit()
            lid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            DB.log('official', proposer_id, proposer_name, '起草法律',
                   f'提交法律《{title}》进入议会投票，截止 {vote_deadline}，生效 {effective_date}')
            return True, f'法律《{title}》已提交议会投票', lid
        finally:
            conn.close()

    @staticmethod
    def list_laws(status=None):
        conn = DB.get_conn(GOV_DB)
        conn.row_factory = sqlite3.Row
        try:
            if status:
                rows = conn.execute("SELECT * FROM laws WHERE status=? ORDER BY id DESC", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM laws ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def law_vote(law_id, official_id, official_name, vote):
        """议员投票：赞成/反对/弃权。"""
        if vote not in ('赞成', '反对', '弃权'):
            return False, '投票无效'
        conn = DB.get_conn(GOV_DB)
        try:
            law = conn.execute("SELECT * FROM laws WHERE id=?", (law_id,)).fetchone()
            if not law:
                return False, '法律不存在'
            if law['status'] != '投票中':
                return False, '该法律已结束投票'
            conn.execute('''INSERT OR REPLACE INTO law_votes (law_id, official_id, official_name, vote, voted_at)
                            VALUES (?,?,?,?,?)''',
                         (law_id, official_id, official_name, vote, now_str()))
            conn.commit()
            return True, f'您已{vote}《{law["title"]}》'
        finally:
            conn.close()

    @staticmethod
    def law_tick():
        """到点自动计票：投票截止后统计赞成/反对/弃权，过半赞成→已通过待发布；否则未通过。"""
        conn = DB.get_conn(GOV_DB)
        try:
            now = now_str()
            pend = conn.execute("SELECT * FROM laws WHERE status='投票中' AND vote_deadline <= ?", (now,)).fetchall()
            msgs = []
            for r in pend:
                votes = conn.execute("SELECT vote FROM law_votes WHERE law_id=?", (r['id'],)).fetchall()
                yes = sum(1 for v in votes if v['vote'] == '赞成')
                no = sum(1 for v in votes if v['vote'] == '反对')
                ab = sum(1 for v in votes if v['vote'] == '弃权')
                # 通过阈值：赞成票严格过半（含弃权不计分母）
                cast = yes + no
                passed = cast > 0 and yes > cast / 2
                new_status = '已通过待发布' if passed else '未通过'
                conn.execute("UPDATE laws SET status=?, yes_count=?, no_count=?, abstain_count=?, voted_at=? WHERE id=?",
                             (new_status, yes, no, ab, now, r['id']))
                msgs.append(f'《{r["title"]}》投票结束：赞成{yes}/反对{no}/弃权{ab} → {new_status}')
            conn.commit()
            return msgs
        finally:
            conn.close()

    @staticmethod
    def law_order_content(law_title, effective_date, publisher_name, n):
        """法律发布行政令正文（蛋国总统行政令标准格式）。
        effective_date 为法律生效日期 YYYY-MM-DD；n 为本年度行政令序号；签发日期取当前日期。"""
        def _cn(d):
            try:
                dt = datetime.strptime(str(d)[:10], '%Y-%m-%d')
            except Exception:
                dt = datetime.now()
            return f'{dt.year} 年 {dt.month} 月 {dt.day} 日'
        eff_cn = _cn(effective_date)
        year = datetime.now().year
        return (
            f'蛋国总统行政令 第总统令 ({year}) {n}号\n'
            f'签发日期：{_cn(datetime.now())} 签发地点：蛋国总统府 签发人：蛋国总统：{publisher_name} 标题：《关于公布〈{law_title}〉的行政令》\n'
            f'序言 依据《蛋国宪法》赋予总统的立法公告权、行政权、国家安全权与行政机构指挥权，为依法公告、落实经议会表决通过的法律，统一全国各机关对该法律的执行标准，现发布本行政令：\n'
            f'第一条 宗旨与适用范围\n'
            f'本行政令旨在：正式公告《{law_title}》文本，明确其生效时间，要求全国各国家机关遵照学习、依法适用该法律。\n'
            f'适用对象：蛋国联邦各行政部门、直属机构；审判、检察机关参照执行；地方行政机关遵照执行。\n'
            f'本令不干涉立法、司法分支独立职权，不得与《蛋国宪法》以及现行有效法律相抵触。\n'
            f'第二条 具体执行条款\n'
            f'【条款 1】《{law_title}》定于{eff_cn} 00:00正式生效，自该时刻起，该法律成为蛋国现行有效法律体系有机组成部分，与现行有效法律拥有同等法律效力；全文以联邦公报登记稿为准。\n'
            f'【条款 2】联邦各行政机关、地方行政单位，应当组织内部人员学习该法律条文，在所有行政决策、行政令签发、政务办理工作中遵守该法律新增规则。\n'
            f'【条款 3】在{eff_cn} 0 时之前已经签发、生效的总统行政令，按照该法律设置的规则自其生效之日起接受审查规制；已经办结的行政行为不溯及既往。\n'
            f'【条款 4】所需宣传、归档、系统适配经费从联邦行政预备金拨付。\n'
            f'第三条 主管与协同机构\n'
            f'主责机关：总统办公室\n'
            f'协同机关：联邦公报局、政务档案总署、联邦信息总局\n'
            f'负责人：{publisher_name}\n'
            f'第四条 生效时间\n'
            f'本行政令自【{eff_cn}】起正式生效；若无特别写明，默认签发当日生效。\n'
            f'第五条 期限（可选）\n'
            f'☑ 长期有效，直至新行政令废止或者国会立法取代\n'
            f'第六条 废止与修订规则\n'
            f'本总统后续可以签发新行政令，修改、暂停、撤销本令全部或部分内容。\n'
            f'若蛋国最高法院裁定本行政令违宪，裁定下达之日本令即刻失效。\n'
            f'蛋国国会可依照宪法立法，覆盖本行政令规定。\n'
            f'第七条 送达与公示\n'
            f'本令交由联邦公报登记、蛋国公民政务平台全文公开，抄送副总统、内阁全体部长、最高法院、议会两院议长。\n'
            f'总统签名：{publisher_name}'
        )

    # ========== 公职申请（民众→政府职位） ==========
    @staticmethod
    def list_departments():
        conn = DB.get_conn(GOV_DB)
        rows = [r[0] for r in conn.execute(
            "SELECT DISTINCT department FROM positions WHERE is_active=1 AND department!='' ORDER BY department")]
        conn.close()
        return rows

    @staticmethod
    def list_positions_for_department(dept):
        """该部门下可选职位（排除总统/副总统）。"""
        conn = DB.get_conn(GOV_DB)
        rows = [r[0] for r in conn.execute(
            "SELECT name FROM positions WHERE is_active=1 AND department=? AND name NOT IN ('总统','副总统') ORDER BY sort_order",
            (dept,))]
        conn.close()
        return rows

    @staticmethod
    def submit_job_app(id_number, name, department, position_name, reason):
        # 硬校验：剥夺政治权利执行期内不得申请公职
        ban = DB.political_ban(id_number)
        if ban.get('banned'):
            return False, ('该身份证号正处于剥夺政治权利执行期（判决 %s，至 %s 期满），'
                           '期满前一律不得担任公职' % (ban.get('case_number') or '—', ban.get('until')))
        conn = DB.get_conn(GOV_DB)
        try:
            # 防重：同部门同职位已有待审/已通过申请
            ex = conn.execute("SELECT 1 FROM public_job_applications WHERE applicant_id_number=? AND department=? AND position_name=? AND status IN ('待审核','已通过')",
                              (id_number, department, position_name)).fetchone()
            if ex:
                return False, '你已申请该职位，请勿重复提交'
            conn.execute('''INSERT INTO public_job_applications
                (applicant_id_number, applicant_name, department, position_name, reason, status, created_at)
                VALUES (?,?,?,?,?, '待审核', ?)''',
                (id_number, name, department, position_name, reason, now_str()))
            conn.commit()
            return True, '公职申请已提交，等待部门主管审核'
        finally:
            conn.close()

    @staticmethod
    def list_my_job_apps(id_number):
        conn = DB.get_conn(GOV_DB)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM public_job_applications WHERE applicant_id_number=? ORDER BY id DESC", (id_number,))]
        conn.close()
        return rows

    @staticmethod
    def list_job_apps_for_reviewer(user):
        """审核人可见的申请：部长/副部长看本部门；总统/副总统看全部。"""
        pos = user.get('position', '')
        conn = DB.get_conn(GOV_DB)
        if pos in ('总统', '副总统'):
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM public_job_applications WHERE status='待审核' ORDER BY id DESC")]
        elif '部长' in pos:
            dept = user.get('department', '')
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM public_job_applications WHERE status='待审核' AND department=? ORDER BY id DESC", (dept,))]
        else:
            rows = []
        conn.close()
        return rows

    @staticmethod
    def review_job_app(app_id, approve, reviewer_id, reviewer_name, note='', term_start='', term_end=''):
        """审核通过：插入 officials 表。"""
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute("SELECT * FROM public_job_applications WHERE id=?", (app_id,)).fetchone()
            if not row:
                return False, '申请不存在'
            if row['status'] != '待审核':
                return False, '该申请已处理'
            if not approve:
                conn.execute("UPDATE public_job_applications SET status='已拒绝', reviewer_id=?, reviewer_name=?, reviewer_note=?, reviewed_at=? WHERE id=?",
                             (reviewer_id, reviewer_name, note, now_str(), app_id))
                conn.commit()
                return True, '已拒绝'
            # 硬校验：审批期间若此人被判剥夺政治权利，也不得任命
            ban = DB.political_ban(row['applicant_id_number'])
            if ban.get('banned'):
                return False, ('%s 正处于剥夺政治权利执行期（判决 %s，至 %s 期满），期满前不得担任公职，'
                               '请拒绝该申请' % (row['applicant_name'], ban.get('case_number') or '—',
                                               ban.get('until')))
            # 通过：插入 officials
            conn.execute('''INSERT INTO officials
                (id_number, name, position, department, rank, status, appointed_date,
                 term_start, term_end, auto_renewal, created_at, last_update)
                VALUES (?,?,?,?,?, '在职', ?, ?, ?, 1, ?, ?)''',
                (row['applicant_id_number'], row['applicant_name'], row['position_name'],
                 row['department'], '普通官员', now_str(), term_start, term_end, now_str(), now_str()))
            oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("UPDATE public_job_applications SET status='已通过', reviewer_id=?, reviewer_name=?, reviewer_note=?, reviewed_at=?, term_start=?, term_end=?, official_id=? WHERE id=?",
                         (reviewer_id, reviewer_name, note, now_str(), term_start, term_end, oid, app_id))
            conn.commit()
            return True, f'已批准，{row["applicant_name"]} 已任命为 {row["department"]}{row["position_name"]}'
        finally:
            conn.close()

    # ========== 人脸比对（入籍申请防重复） ==========
    IMG_EXT = ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp')
    DOC_EXT = ('.docx', '.doc', '.odt', '.rtf')
    PDF_EXT = ('.pdf',)

    @staticmethod
    def face_available():
        try:
            import cv2, numpy  # noqa: F401
        except Exception as e:
            return False, '%s（请先 pip install opencv-python numpy）' % e
        if DB._face_detector() is None and DB._cascade() is None:
            return False, ('缺少人脸检测模型：请确认 tools 目录下有 face_detection_yunet_2023mar.onnx'
                           '（或 haarcascade_frontalface_default.xml）')
        return True, ''

    @staticmethod
    def _cascade():
        """Haar 人脸检测器（懒加载）。优先用程序自带 tools 目录下的模型文件，
        这样即使解释器里的 opencv 缺 data 目录也能用。"""
        import cv2
        if getattr(DB._cascade, 'obj', None) is None and not getattr(DB._cascade, 'tried', False):
            DB._cascade.tried = True
            DB._cascade.obj = None
            if not hasattr(cv2, 'CascadeClassifier'):
                return None
            cands = []
            # OpenCV 的 FileStorage 读不了中文路径，所以把模型复制到纯英文的临时目录再加载
            src = os.path.join(BASE_DIR, 'tools', 'haarcascade_frontalface_default.xml')
            if os.path.isfile(src):
                try:
                    import tempfile
                    import shutil
                    tdir = os.path.join(tempfile.gettempdir(), 'dgov_face_model')
                    os.makedirs(tdir, exist_ok=True)
                    dst = os.path.join(tdir, 'haarcascade_frontalface_default.xml')
                    if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
                        shutil.copyfile(src, dst)
                    cands.append(dst)
                except Exception:
                    pass
            try:
                cands.append(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            except Exception:
                pass
            cands.append(r'C:\Users\mac\AppData\Local\Programs\Python\Python311\Lib\site-packages'
                         r'\cv2\data\haarcascade_frontalface_default.xml')
            for p in cands:
                try:
                    if p and os.path.isfile(p):
                        cas = cv2.CascadeClassifier(p)
                        if not cas.empty():
                            DB._cascade.obj = cas
                            break
                except Exception:
                    continue
        return getattr(DB._cascade, 'obj', None)

    @staticmethod
    def _face_detector():
        """YuNet 人脸检测器（首选：检测更准，且 cv2 4.x / 5.x 都能用）。模型放 tools 目录，
        因为 OpenCV 读不了中文路径，加载前先复制到纯英文临时目录。"""
        import cv2
        if getattr(DB._face_detector, 'tried', False):
            return getattr(DB._face_detector, 'obj', None)
        DB._face_detector.tried = True
        DB._face_detector.obj = None
        if not hasattr(cv2, 'FaceDetectorYN'):
            return None
        try:
            import tempfile
            import shutil
            src = os.path.join(BASE_DIR, 'tools', 'face_detection_yunet_2023mar.onnx')
            if not os.path.isfile(src):
                return None
            tdir = os.path.join(tempfile.gettempdir(), 'dgov_face_model')
            os.makedirs(tdir, exist_ok=True)
            dst = os.path.join(tdir, 'face_detection_yunet_2023mar.onnx')
            if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
                shutil.copyfile(src, dst)
            DB._face_detector.obj = cv2.FaceDetectorYN.create(dst, '', (320, 320), 0.6, 0.3, 5000)
        except Exception:
            DB._face_detector.obj = None
        return DB._face_detector.obj

    @staticmethod
    def _face_vec(raw):
        """图片字节 → {'vec': 128×128 灰度特征, 'detected': bool}；读不出来返回 None"""
        import cv2
        import numpy as np
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        detected = False
        box = None
        yun = DB._face_detector()
        if yun is not None:
            try:
                h, w = img.shape[:2]
                yun.setInputSize((int(w), int(h)))
                ret = yun.detect(img)
                faces = ret[1] if ret is not None and len(ret) > 1 else None
                if faces is not None and len(faces):
                    f = max(faces, key=lambda r: float(r[2]) * float(r[3]))
                    box = tuple(int(v) for v in f[:4])
                    detected = True
            except Exception:
                box = None
        if box is None:
            cas = DB._cascade()
            if cas is not None and not cas.empty():
                try:
                    faces = cas.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4,
                                                 minSize=(40, 40))
                except Exception:
                    faces = []
                if len(faces):
                    x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
                    box = (int(x), int(y), int(w), int(h))
                    detected = True
        if box:
            x, y, w, h = box
            x, y = max(0, x), max(0, y)
            if w > 4 and h > 4:
                gray = gray[y:y + h, x:x + w]
        gray = cv2.resize(gray, (128, 128))
        gray = cv2.equalizeHist(gray)
        return {'vec': gray.astype(np.float32), 'detected': detected}

    @staticmethod
    def _face_sim(a, b):
        """归一化相关系数，越接近 1 越像"""
        import numpy as np
        a = a - a.mean()
        b = b - b.mean()
        na = float(np.linalg.norm(a))
        nb = float(np.linalg.norm(b))
        if na == 0 or nb == 0:
            return 0.0
        return float((a * b).sum() / (na * nb))

    @staticmethod
    def sync_faces_from_files(extra_dirs=None):
        """把户籍库里只有本地路径、还没有 b64 的人脸照片读出来写入云端 face_b64。
        返回 (成功数, 找不到文件数)。可重复执行，不会覆盖已有 b64。"""
        dirs = []
        for d in [BASE_DIR] + list(extra_dirs or []) + \
                 [r'D:\Yuan Honghan\学籍系统', r'D:\Yuan Honghan\联网版系统']:
            if d and d not in dirs:
                dirs.append(d)
        ok = fail = 0
        try:
            hconn = DB.get_conn(HOUSEHOLD_DB)
            rows = [dict(r) for r in hconn.execute(
                "SELECT id_number, face_path FROM face_info "
                "WHERE face_b64 IS NULL OR face_b64=''").fetchall()]
        except Exception:
            return 0, 0
        for r in rows:
            p = (r.get('face_path') or '').strip()
            cand = []
            if p:
                cand.append(p)
                for d in dirs:
                    cand.append(os.path.join(d, p.replace('/', os.sep)))
            hit = ''
            for cp in cand:
                try:
                    if os.path.isfile(cp):
                        hit = cp
                        break
                except Exception:
                    continue
            if not hit:
                fail += 1
                continue
            try:
                with open(hit, 'rb') as f:
                    raw = f.read()
                b64 = base64.b64encode(raw).decode('ascii')
                hconn.execute("UPDATE face_info SET face_b64=? WHERE id_number=?",
                              (b64, r.get('id_number')))
                hconn.commit()
                ok += 1
            except Exception:
                fail += 1
        try:
            hconn.close()
        except Exception:
            pass
        return ok, fail

    @staticmethod
    def load_face_gallery():
        """云端户籍库的全部人脸 → [(id_number, name, vec)]"""
        out = []
        rows = []
        try:
            hconn = DB.get_conn(HOUSEHOLD_DB)
            for sql in ("SELECT f.id_number, f.face_b64, r.name FROM face_info f "
                        "LEFT JOIN residents r ON r.id_number = f.id_number",
                        "SELECT f.id_number, '' AS face_b64, r.name FROM face_info f "
                        "LEFT JOIN residents r ON r.id_number = f.id_number"):
                try:
                    rows = [dict(x) for x in hconn.execute(sql).fetchall()]
                    break
                except Exception:
                    continue
            hconn.close()
        except Exception:
            return out
        for r in rows:
            b64 = (r.get('face_b64') or '').strip()
            if not b64:
                continue
            try:
                raw = base64.b64decode(b64)
            except Exception:
                continue
            v = DB._face_vec(raw)
            if v:
                out.append((r.get('id_number') or '', r.get('name') or '', v['vec']))
        return out

    @staticmethod
    def face_identify(raw, threshold=0.86):
        """与户籍库所有人脸比对。返回 {'ok':bool,'matched':bool,'score':float,'reason':str,...}"""
        ok_cv, err = DB.face_available()
        if not ok_cv:
            return {'ok': False, 'matched': False, 'score': 0.0, 'reason': '人脸识别组件不可用：' + err}
        mine = DB._face_vec(raw)
        if not mine:
            return {'ok': False, 'matched': False, 'score': 0.0, 'reason': '这张图片读不出来，请换一张 jpg/png'}
        if not mine['detected']:
            if DB._face_detector() is not None or DB._cascade() is not None:
                return {'ok': False, 'matched': False, 'score': 0.0,
                        'reason': '照片里没检测到人脸，请上传清晰的正面免冠照'}
            # 当前解释器的 opencv 既无人脸检测模型也不支持 → 退回整图特征比对（仍然严格查重）
        gallery = DB.load_face_gallery()
        best = (0.0, '', '')
        for idn, nm, vec in gallery:
            s = DB._face_sim(mine['vec'], vec)
            if s > best[0]:
                best = (s, idn, nm)
        if best[0] >= threshold:
            return {'ok': False, 'matched': True, 'score': best[0], 'id_number': best[1],
                    'name': best[2],
                    'reason': '人脸重复：这张脸已登记在户籍库（%s %s），不能再次申请入籍' % (best[2], best[1])}
        return {'ok': True, 'matched': False, 'score': best[0], 'gallery': len(gallery), 'reason': ''}

    @staticmethod
    def passport_in_use(passport):
        """护照号是否已被某份有效入籍申请占用"""
        p = (passport or '').strip()
        if not p:
            return None
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute(
                "SELECT applicant_name, status, temp_account FROM nationality_applications "
                "WHERE TRIM(foreign_passport_no)=? AND status NOT IN ('已拒绝','已撤销') "
                "ORDER BY id DESC LIMIT 1", (p,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ========== 补充材料（按类型校验 + 云端存 b64） ==========
    @staticmethod
    def parse_requirements(text):
        """审核人填的要求逐行解析：名称|类型（类型：图片 / 文档 / PDF / 任意）"""
        items = []
        for line in (text or '').splitlines():
            line = line.strip()
            if not line:
                continue
            name, typ = line, '任意'
            for sep in ('|', '｜'):
                if sep in line:
                    name, typ = line.split(sep, 1)
                    break
            name, typ = name.strip(), (typ or '任意').strip()
            if name:
                items.append({'item': name, 'type': typ})
        return items

    @staticmethod
    def _type_ok(req_type, filename):
        ext = os.path.splitext(filename or '')[1].lower()
        t = (req_type or '任意').strip()
        if t in ('图片', '照片', 'image', 'photo', 'jpg', 'png'):
            return ext in DB.IMG_EXT
        if t in ('文档', 'docx', 'Word', 'word'):
            return ext in DB.DOC_EXT
        if t in ('PDF', 'pdf'):
            return ext in DB.PDF_EXT
        return True

    @staticmethod
    def expected_type_label(req_type):
        return {'图片': '图片（jpg/png）', '照片': '图片（jpg/png）', '文档': 'Word 文档（docx）',
                'docx': 'Word 文档（docx）', 'PDF': 'PDF 文件', 'pdf': 'PDF 文件'}.get(
            (req_type or '').strip(), '任意格式')

    @staticmethod
    def upload_attachment(app_id, req_item, req_type, path):
        """上传一项补充材料：类型不符直接拒绝；文件本体以 b64 写入云端"""
        if not path or not os.path.isfile(path):
            return False, '文件不存在'
        name = os.path.basename(path)
        if not DB._type_ok(req_type, name):
            return False, '这一项要求上传%s，你选的是「%s」，文件类型不符' % (
                DB.expected_type_label(req_type), os.path.splitext(name)[1] or name)
        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0
        if size > 20 * 1024 * 1024:
            return False, '文件太大（%.1f MB），请压缩到 20MB 以内' % (size / 1048576.0)
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except Exception as e:
            return False, '读取文件失败：%s' % e
        ext = os.path.splitext(name)[1].lower()
        mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
                '.pdf': 'application/pdf',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.doc': 'application/msword'}.get(ext, 'application/octet-stream')
        b64 = base64.b64encode(raw).decode('ascii')
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("DELETE FROM nationality_attachments WHERE app_id=? AND req_item=?",
                         (app_id, req_item))
            conn.execute("INSERT INTO nationality_attachments "
                         "(app_id, req_item, file_name, file_ext, mime, data_b64, uploaded_at) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (app_id, req_item, name, ext, mime, b64, now_str()))
            conn.commit()
            return True, '已上传：%s' % name
        finally:
            conn.close()

    @staticmethod
    def list_attachments(app_id):
        """某份申请已上传的附件（不含 b64 本体）"""
        conn = DB.get_conn(GOV_DB)
        try:
            rows = conn.execute(
                "SELECT id, app_id, req_item, file_name, file_ext, mime, uploaded_at, "
                "LENGTH(data_b64) AS b64_len FROM nationality_attachments "
                "WHERE app_id=? ORDER BY id", (app_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def attachment_blob(att_id):
        """取附件 b64 本体"""
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute("SELECT file_name, file_ext, mime, data_b64 "
                               "FROM nationality_attachments WHERE id=?", (att_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ========== 剥夺政治权利（读法院判决） ==========
    @staticmethod
    def _add_months(date_str, months):
        import calendar
        from datetime import datetime, timedelta
        s = (date_str or '')[:10]
        try:
            d = datetime.strptime(s, '%Y-%m-%d')
        except Exception:
            return ''
        m = int(months or 0)
        if m <= 0:
            return ''
        y = d.year + (d.month - 1 + m) // 12
        mo = (d.month - 1 + m) % 12 + 1
        day = min(d.day, calendar.monthrange(y, mo)[1])
        return datetime(y, mo, day).strftime('%Y-%m-%d')

    @staticmethod
    def political_ban(id_number):
        """某人是否在剥夺政治权利执行期内。返回 {'banned','until','case_number','months'}"""
        res = {'banned': False, 'until': '', 'case_number': '', 'months': 0}
        idn = (id_number or '').strip()
        if not idn:
            return res
        try:
            conn = DB.get_conn(COURT_DB)
        except Exception:
            return res
        try:
            cmap = {}
            for row in conn.execute("SELECT case_number, defendant_id, plaintiff_id FROM cases").fetchall():
                d = dict(row)
                cmap[d.get('case_number')] = d
            rows = [dict(r) for r in conn.execute("SELECT * FROM judgments").fetchall()]
        except Exception:
            rows = []
            cmap = {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        today = datetime.now().strftime('%Y-%m-%d')
        for j in rows:
            c = cmap.get(j.get('case_number')) or {}
            target = j.get('sentence_target') or ''
            cand = []
            if target in ('被告', '被告和原告') and int(j.get('deprive_political_defendant') or 0) > 0:
                cand.append((int(j['deprive_political_defendant']),
                             (j.get('deprive_defendant_id') or c.get('defendant_id') or '')))
            if target in ('原告', '被告和原告') and int(j.get('deprive_political_plaintiff') or 0) > 0:
                cand.append((int(j['deprive_political_plaintiff']),
                             (j.get('deprive_plaintiff_id') or c.get('plaintiff_id') or '')))
            for months, pid in cand:
                if (pid or '').strip() != idn:
                    continue
                until = DB._add_months(j.get('judgment_date'), months)
                if until and until >= today:
                    res['banned'] = True
                    res['until'] = until
                    res['case_number'] = j.get('case_number') or ''
                    res['months'] = months
                    return res
        return res

    # ========== 国籍申请 ==========
    @staticmethod
    def submit_nationality_app(name, original_nationality, birth_date, gender, reason,
                               foreign_passport_no, face_b64=''):
        """提交入籍申请，生成随机临时账号密码。返回 (ok, msg, account, password, app_id)。
        提交前两道校验：护照号不得与现有申请重复；申请人脸不得已在户籍库登记。"""
        import random, string
        passport = (foreign_passport_no or '').strip()
        if passport:
            dup = DB.passport_in_use(passport)
            if dup:
                return (False, '护照号「%s」已出现在另一份入籍申请里（申请人：%s，状态：%s），不能重复申请'
                        % (passport, dup.get('applicant_name') or '—', dup.get('status') or '—'), '', '', 0)
        if not (face_b64 or '').strip():
            return False, '未完成人脸识别，不能提交申请', '', '', 0
        # 人脸硬校验：与户籍库全部人脸比对，只要有匹配就拒绝（不依赖界面，绕不过）
        try:
            _raw = base64.b64decode((face_b64 or '').strip())
            _fi = DB.face_identify(_raw)
            if not _fi.get('ok'):
                return False, _fi.get('reason') or '人脸校验未通过，不能提交申请', '', '', 0
        except Exception as _fe:
            return False, '人脸照片解析失败：%s' % _fe, '', '', 0
        conn = DB.get_conn(GOV_DB)
        try:
            # 生成临时账号：DG 前缀 + 8 位随机
            while True:
                acc = 'DG' + ''.join(random.choices(string.digits, k=8))
                pw = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
                ex = conn.execute("SELECT 1 FROM nationality_applications WHERE temp_account=?", (acc,)).fetchone()
                if not ex:
                    break
            now = now_str()
            conn.execute('''INSERT INTO nationality_applications
                (applicant_name, original_nationality, birth_date, gender, reason, foreign_passport_no,
                 temp_account, temp_password, status, created_at, face_b64)
                VALUES (?,?,?,?,?,?,?,?, '待审核', ?, ?)''',
                (name, original_nationality, birth_date, gender, reason, passport,
                 acc, pw, now, (face_b64 or '')))
            conn.commit()
            app_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return True, '申请已提交，请牢记临时账号密码', acc, pw, app_id
        finally:
            conn.close()

    @staticmethod
    def temp_login(account, password):
        """临时账号登录。返回 row dict 或 None。"""
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute("SELECT * FROM nationality_applications WHERE temp_account=? AND temp_password=? AND used=0",
                               (account, password)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def list_nationality_pending():
        conn = DB.get_conn(GOV_DB)
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM nationality_applications WHERE status IN ('待审核','待补充材料') "
                "ORDER BY (status='待审核') DESC, id DESC")]
        finally:
            conn.close()

    @staticmethod
    def review_nationality(app_id, approve, reviewer_id, reviewer_name, note=''):
        """审核：通过则生成身份证号 + 写户籍库。返回 (ok, msg, id_number)。"""
        import random
        conn = DB.get_conn(GOV_DB)
        hconn = DB.get_conn(HOUSEHOLD_DB)
        try:
            row = conn.execute("SELECT * FROM nationality_applications WHERE id=?", (app_id,)).fetchone()
            if not row:
                return False, '申请不存在', ''
            if row['status'] != '待审核':
                return False, f'该申请已是「{row["status"]}」状态，不能重复审核', ''
            if not approve:
                conn.execute("UPDATE nationality_applications SET status='已拒绝', reviewer_id=?, reviewer_name=?, reviewer_note=?, reviewed_at=? WHERE id=?",
                             (reviewer_id, reviewer_name, note, now_str(), app_id))
                conn.commit()
                return True, '已拒绝', ''
            # 通过：生成身份证号 990001 + 生日(YYYYMMDD) + 4位随机
            bd = (row['birth_date'] or '2000-01-01').replace('-', '')
            while True:
                tail = ''.join(random.choices('0123456789', k=4))
                id_num = f'990001{bd}{tail}'
                ex = hconn.execute("SELECT 1 FROM residents WHERE id_number=?", (id_num,)).fetchone()
                if not ex:
                    break
            now = now_str()
            hconn.execute('''INSERT INTO residents (id_number, name, gender, birth_date, address, registered_date, last_update, status)
                             VALUES (?,?,?,?,?,?,?, '正常')''',
                          (id_num, row['applicant_name'], row['gender'] or '未知',
                           row['birth_date'], '蛋国首都中央区（新入籍）', now, now))
            hconn.commit()
            conn.execute("UPDATE nationality_applications SET status='已通过', assigned_id_number=?, reviewer_id=?, reviewer_name=?, reviewer_note=?, reviewed_at=? WHERE id=?",
                         (id_num, reviewer_id, reviewer_name, note, now, app_id))
            conn.commit()
            bank_note = ''
            try:
                bok, bmsg, bcard = DB.open_bank_account_for_new_citizen(id_num, row['applicant_name'])
                if bok:
                    bank_note = '；' + bmsg
                    try:
                        DB.send_message('citizen', id_num, '【蛋国银行】开户通知',
                                        '欢迎成为蛋国公民。已为你自动开立储蓄账户，卡号 %s，初始资金 10000 元。' % bcard)
                    except Exception:
                        pass
                else:
                    bank_note = '；银行开户未完成：' + bmsg
            except Exception as e:
                bank_note = '；银行开户异常：%s' % e
            return True, '审核通过，已写入户籍系统' + bank_note, id_num
        finally:
            hconn.close()
            conn.close()

    @staticmethod
    def open_bank_account_for_new_citizen(id_number, name, initial=10000.0, phone=''):
        """国籍申请审核通过后自动开立蛋国银行账户（初始资金 10000 元）。返回 (ok, msg, card)。"""
        import random
        bconn = DB.get_conn(BANK_DB)
        try:
            ex = bconn.execute("SELECT * FROM customers WHERE id_number=? AND IFNULL(is_enterprise,0)=0",
                               (id_number,)).fetchone()
            if ex:
                return False, '该身份证号已有银行账户（卡号 %s），不重复开户' % ex['card_number'], ex['card_number']
            card = ''
            for _ in range(30):
                card = '6222' + ''.join(random.choices('0123456789', k=12))
                if not bconn.execute("SELECT 1 FROM customers WHERE card_number=?", (card,)).fetchone():
                    break
            now = now_str()
            bconn.execute("""INSERT INTO customers (card_number, id_number, name, password, balance,
                            credit_score, account_type, phone, email, address, create_time,
                            last_update_time, is_frozen, failed_attempts, credit_limit, is_enterprise)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,0)""",
                          (card, id_number, name, '', float(initial), 650, '储蓄账户', phone or '', '',
                           '蛋国首都中央区（新入籍自动开户）', now, now))
            try:
                bconn.execute("""INSERT INTO transactions (from_card,to_card,amount,transaction_type,transaction_time)
                                 VALUES ('SYSTEM-OPEN',?,?,?,?)""",
                              (card, float(initial), '开户初始资金', now))
            except Exception:
                pass
            try:
                bconn.execute("""INSERT INTO operation_logs (operator_type,operator_id,operation_type,details,operation_time,ip_address)
                                 VALUES ('system',0,'自动开户',?,?,'')""",
                              ('国籍审核通过自动开户：%s / %s，初始资金 %.0f' % (name, id_number, float(initial)), now))
            except Exception:
                pass
            bconn.commit()
            return True, '已开立蛋国银行账户（卡号 %s，初始资金 %.0f 元）' % (card, float(initial)), card
        finally:
            bconn.close()

    @staticmethod
    def request_materials(app_id, items_text, reviewer_name):
        """审核人要求补充材料：把申请状态改为'待补充材料'，并按新要求清空上一轮附件。"""
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute("SELECT * FROM nationality_applications WHERE id=?", (app_id,)).fetchone()
            if not row:
                return False, '申请不存在'
            row = dict(row)
            if row.get('status') != '待审核':
                return False, f'当前状态「{row.get("status")}」不能要求补充材料'
            conn.execute("DELETE FROM nationality_attachments WHERE app_id=?", (app_id,))
            conn.execute('''UPDATE nationality_applications SET
                status='待补充材料', requested_materials=?, materials_status='待补充',
                submitted_materials='', materials_at='',
                reviewer_id=?, reviewer_name=?, reviewer_note=?, reviewed_at=? WHERE id=?''',
                (items_text, row['reviewer_id'] or 0, reviewer_name,
                 '要求补充材料', now_str(), app_id))
            conn.commit()
            return True, '已通知申请人补充材料'
        finally:
            conn.close()

    @staticmethod
    def submit_materials(app_id, materials_text=''):
        """老外提交补充材料 → 状态回到'待审核'。审核人列明的每一项都必须已上传才算齐。"""
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute("SELECT * FROM nationality_applications WHERE id=?", (app_id,)).fetchone()
            if not row:
                return False, '申请不存在'
            row = dict(row)
            if row.get('status') != '待补充材料':
                return False, f'当前状态「{row.get("status")}」无需补充材料'
            reqs = DB.parse_requirements(row.get('requested_materials') or '')
            have = set(a.get('req_item') or '' for a in DB.list_attachments(app_id))
            missing = [r['item'] for r in reqs if r['item'] not in have]
            if missing:
                return False, '还有 %d 项材料没有上传：%s' % (len(missing), '、'.join(missing))
            conn.execute('''UPDATE nationality_applications SET
                status='待审核', submitted_materials=?, materials_status='已补充', materials_at=? WHERE id=?''',
                ((materials_text or ''), now_str(), app_id))
            conn.commit()
            return True, '补充材料已提交，等待再次审核'
        finally:
            conn.close()

    @staticmethod
    def issue_certificate_serial(id_number, name):
        """生成国籍证明防伪编号 NG-YYYYMMDD-XXXX，写库并返回编号。"""
        import random
        conn = DB.get_conn(GOV_DB)
        try:
            today = now_str()[:10].replace('-', '')
            while True:
                tail = ''.join(random.choices('0123456789', k=4))
                serial = f'NG-{today}-{tail}'
                ex = conn.execute("SELECT 1 FROM certificate_serials WHERE serial=?", (serial,)).fetchone()
                if not ex:
                    break
            conn.execute("INSERT INTO certificate_serials (serial, id_number, name, issued_at) VALUES (?,?,?,?)",
                         (serial, id_number, name, now_str()))
            conn.commit()
            return serial
        finally:
            conn.close()

    @staticmethod
    def confirm_nationality(app_id):
        """用户确认入籍 → 注销临时账号。"""
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("UPDATE nationality_applications SET status='已确认入籍', used=1, confirmed_at=? WHERE id=?",
                         (now_str(), app_id))
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def withdraw_nationality(app_id):
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("UPDATE nationality_applications SET status='已撤销', used=1 WHERE id=?", (app_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def cert_verify(serial='', name='', id_number=''):
        """国籍证明查验（网页端调用）：校验防伪编号，并到户籍库查该人身份信息。
        返回 dict：{'ok':bool, 'error':str, 'cert':{...}, 'resident':{...}, 'passports':[...]}"""
        serial = (serial or '').strip()
        name = (name or '').strip()
        id_number = (id_number or '').strip()
        if not (serial or id_number):
            return {'ok': False, 'error': '请输入防伪编号或身份证号后再查询'}
        cert = None
        try:
            conn = DB.get_conn(GOV_DB)
            if serial:
                row = conn.execute(
                    "SELECT serial,id_number,name,issued_at FROM certificate_serials WHERE serial=?",
                    (serial,)).fetchone()
                cert = dict(row) if row else None
            conn.close()
        except Exception as e:
            return {'ok': False, 'error': '读取证明登记失败：%s' % e}
        if serial and not cert:
            return {'ok': False, 'error': '未找到防伪编号 %s 的登记记录，该证明可能不是本系统签发的' % serial}
        if cert:
            if id_number and cert.get('id_number') and id_number != cert['id_number']:
                return {'ok': False, 'error': '防伪编号与填写的身份证号不一致，请核对后再查'}
            id_number = id_number or (cert.get('id_number') or '')
            name = name or (cert.get('name') or '')
        resident = {}
        try:
            hconn = DB.get_conn(HOUSEHOLD_DB)
            hconn.row_factory = sqlite3.Row
            if name:
                row = hconn.execute("SELECT * FROM residents WHERE id_number=? AND name=?",
                                    (id_number, name)).fetchone()
            else:
                row = hconn.execute("SELECT * FROM residents WHERE id_number=?", (id_number,)).fetchone()
            resident = dict(row) if row else {}
            hconn.close()
        except Exception as e:
            return {'ok': False, 'error': '查询户籍库失败：%s' % e}
        if not resident:
            return {'ok': False,
                    'error': '户籍登记库中未找到该身份信息（身份证号 %s）' % id_number}
        try:
            passports = DB.list_passports_for(id_number)
        except Exception:
            passports = []
        return {'ok': True, 'cert': cert or {}, 'resident': resident, 'passports': passports}

    @staticmethod
    def list_passports_for(id_number):
        """查出入境库该身份证号名下所有护照。"""
        try:
            iconn = DB.get_conn(os.path.join(BASE_DIR, 'immigration_system.db'))
            rows = [dict(r) for r in iconn.execute(
                "SELECT * FROM passports WHERE id_number=? ORDER BY issue_date DESC", (id_number,))]
            iconn.close()
            return rows
        except Exception:
            return []

    @staticmethod
    def tally_and_publish(law_id, publisher_id, publisher_name):
        """总统手动：立即对一条法律计票，通过则直接签行政令（生效时间仍按 effective_date）。
        未通过则置为'未通过'。"""
        conn = DB.get_conn(GOV_DB)
        try:
            law = conn.execute("SELECT * FROM laws WHERE id=?", (law_id,)).fetchone()
            if not law:
                return False, '法律不存在', None
            if law['status'] not in ('投票中', '已通过待发布'):
                return False, '该法律已结束，不能重复计票', None
            votes = conn.execute("SELECT vote FROM law_votes WHERE law_id=?", (law_id,)).fetchall()
            yes = sum(1 for v in votes if v['vote'] == '赞成')
            no = sum(1 for v in votes if v['vote'] == '反对')
            ab = sum(1 for v in votes if v['vote'] == '弃权')
            cast = yes + no
            passed = cast > 0 and yes > cast / 2
            if not passed:
                conn.execute("UPDATE laws SET status='未通过', yes_count=?, no_count=?, abstain_count=?, voted_at=? WHERE id=?",
                             (yes, no, ab, now_str(), law_id))
                conn.commit()
                return False, f'计票结果：赞成{yes}/反对{no}/弃权{ab}，未通过议会', None
            # 通过 → 直接签行政令（标准格式：蛋国总统行政令）
            n = conn.execute("SELECT COUNT(*) FROM executive_orders").fetchone()[0] + 1
            order_no = f'总统令 ({datetime.now():%Y}) {n}号'
            content = DB.law_order_content(law["title"], law["effective_date"], publisher_name, n)
            conn.execute('''INSERT INTO executive_orders (order_no, title, content, publisher_id, publisher_name,
                             publisher_position, effective_mode, effective_time, is_withdrawn, created_at)
                            VALUES (?,?,?,?,?,?,?,?,0,?)''',
                         (order_no, f'关于公布〈{law["title"]}〉的行政令', content,
                          publisher_id, publisher_name, '总统', '定时生效',
                          law['effective_date'] + ' 00:00', now_str()))
            order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("UPDATE laws SET status='已生效', yes_count=?, no_count=?, abstain_count=?, voted_at=?, admin_order_id=?, published_at=? WHERE id=?",
                         (yes, no, ab, now_str(), order_id, now_str(), law_id))
            conn.commit()
            DB.log('official', publisher_id, publisher_name, '发布法律',
                   f'手动计票并发布《{law["title"]}》：赞成{yes}/反对{no}/弃权{ab}，签发{order_no}，生效 {law["effective_date"]}')
            return True, f'计票通过（赞成{yes}/反对{no}/弃权{ab}），已签发{order_no}，生效 {law["effective_date"]}', order_id
        finally:
            conn.close()

    @staticmethod
    def confirm_publish_law(law_id, publisher_id, publisher_name):
        """总统确认发布：系统自动签行政令（总统名字），法律状态→已生效。"""
        conn = DB.get_conn(GOV_DB)
        try:
            law = conn.execute("SELECT * FROM laws WHERE id=?", (law_id,)).fetchone()
            if not law:
                return False, '法律不存在'
            if law['status'] != '已通过待发布':
                return False, '法律尚未通过议会投票，不能发布'
            # 自动签行政令（标准格式：蛋国总统行政令）
            n = conn.execute("SELECT COUNT(*) FROM executive_orders").fetchone()[0] + 1
            order_no = f'总统令 ({datetime.now():%Y}) {n}号'
            content = DB.law_order_content(law["title"], law["effective_date"], publisher_name, n)
            conn.execute('''INSERT INTO executive_orders (order_no, title, content, publisher_id, publisher_name,
                             publisher_position, effective_mode, effective_time, is_withdrawn, created_at)
                            VALUES (?,?,?,?,?,?,?,?,0,?)''',
                         (order_no, f'关于公布〈{law["title"]}〉的行政令', content,
                          publisher_id, publisher_name, '总统', '定时生效',
                          law['effective_date'] + ' 00:00', now_str()))
            order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("UPDATE laws SET status='已生效', admin_order_id=?, published_at=? WHERE id=?",
                         (order_id, now_str(), law_id))
            conn.commit()
            DB.log('official', publisher_id, publisher_name, '发布法律',
                   f'《{law["title"]}》正式发布，签发{order_no}，生效 {law["effective_date"]}')
            return True, f'《{law["title"]}》已发布，签发 {order_no}'
        finally:
            conn.close()

    @staticmethod
    def appoint_official(id_number, name, gender, position, department, rank, phone, address,
                         term_start, term_end, op_type, op_id, op_name):
        """任命官员：写入 officials（含任期）→ 官员档案“任职”履历 → 待签署协议。
        返回 (ok, msg, oid)。"""
        now = now_str()
        # 硬校验：总统、副总统只能通过大选产生，不允许走「官员任命」直接指派
        if position in ('总统', '副总统'):
            return False, ('「%s」不能通过官员任命直接指派，只能通过总统大选产生'
                           '（候选人登记 → 审核 → 投票 → 就职）' % position), None
        # 硬校验：剥夺政治权利执行期内不得任命为官员
        ban = DB.political_ban(id_number)
        if ban.get('banned'):
            return False, ('该人正处于剥夺政治权利执行期（判决 %s，至 %s 期满），'
                           '期满前不得任命为任何官员' % (ban.get('case_number') or '—', ban.get('until'))), None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            exist = c.execute("SELECT * FROM officials WHERE id_number=?", (id_number,)).fetchone()
            if exist:
                return False, '该人员已是政府官员，请在官员管理中操作', None
            c.execute('''INSERT INTO officials (id_number,name,gender,position,department,rank,status,
                appointed_date,phone,address,created_at,last_update,term_start,term_end)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (id_number, name, gender, position, department, rank, '在职', now,
                       phone, address, now, now, term_start, term_end))
            oid = c.lastrowid
            DB._add_career(c, oid, '任职', position, department, term_start, term_end,
                           f'首次任命：{department}{position}')
            DB._new_agreement(c, oid, position, department, term_start, term_end)
            conn.commit()
            DB.log(op_type, op_id, op_name, '添加官员',
                   f'添加官员{name}，职务{position}，部门{department}，任期{term_start}~{term_end}')
            return True, '任命成功', oid
        finally:
            conn.close()

    @staticmethod
    def adjust_official(oid, position, department, rank, term_start, term_end,
                        op_type, op_id, op_name):
        """调整职务/级别：更新 officials → 档案“任职”履历 → 新待签署协议。
        返回 (ok, msg)。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            r = c.execute("SELECT * FROM officials WHERE id=?", (oid,)).fetchone()
            if not r:
                return False, '官员不存在'
            old_pos, old_dept = r['position'], r['department']
            now = now_str()
            c.execute('''UPDATE officials SET position=?, department=?, rank=?, last_update=?,
                         term_start=?, term_end=? WHERE id=?''',
                      (position, department, rank, now, term_start, term_end, oid))
            note = f'职务调整：{old_dept}{old_pos} → {department}{position}'
            if rank != r['rank']:
                note += f'，级别 {r["rank"]} → {rank}'
            DB._add_career(c, oid, '任职', position, department, term_start, term_end, note)
            DB._new_agreement(c, oid, position, department, term_start, term_end)
            conn.commit()
            DB.log(op_type, op_id, op_name, '调整职务',
                   f'官员{r["name"]}调整为{department}{position}，任期{term_start}~{term_end}')
            return True, '职务已调整，新任期待签署协议将在其下次登录时提示'
        finally:
            conn.close()

    @staticmethod
    def set_tenure(oid, term_start, term_end, op_type, op_id, op_name):
        """上级在“任期管理”中调整某官员任期：更新任期 → 档案履历 → 新待签署协议。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            r = c.execute("SELECT * FROM officials WHERE id=?", (oid,)).fetchone()
            if not r:
                return False, '官员不存在'
            old_ts, old_te = r['term_start'], r['term_end']
            c.execute("UPDATE officials SET term_start=?, term_end=?, last_update=? WHERE id=?",
                      (term_start, term_end, now_str(), oid))
            DB._add_career(c, oid, '任职', r['position'], r['department'], term_start, term_end,
                           f'上级调整任期：{old_ts}~{old_te} → {term_start}~{term_end}')
            DB._new_agreement(c, oid, r['position'], r['department'], term_start, term_end)
            conn.commit()
            DB.log(op_type, op_id, op_name, '调整任期', f'官员{r["name"]}任期调整为{term_start}~{term_end}')
            return True, f'官员 {r["name"]} 任期已调整为 {term_start} 至 {term_end}，其下次登录需重新签署协议'
        finally:
            conn.close()

    @staticmethod
    def set_tenure_batch(oid_list, term_start, term_end, op_type, op_id, op_name):
        """批量调整任期：对多个官员一次性设置任期（总统/副总统自动跳过）。
        返回 (ok, msg, done_count, skipped_names)。"""
        if not oid_list:
            return False, '未选择官员', 0, []
        try:
            datetime.strptime(term_start, '%Y-%m-%d')
            datetime.strptime(term_end, '%Y-%m-%d')
        except Exception:
            return False, '请按 YYYY-MM-DD 格式填写日期', 0, []
        if term_end < term_start:
            return False, '卸任日期不能早于起任日期', 0, []
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        done, skipped = 0, []
        try:
            for oid in oid_list:
                r = c.execute("SELECT * FROM officials WHERE id=?", (oid,)).fetchone()
                if not r:
                    continue
                if r['position'] in ('总统', '副总统'):
                    skipped.append(r['name'])
                    continue
                old_ts, old_te = r['term_start'], r['term_end']
                c.execute("UPDATE officials SET term_start=?, term_end=?, last_update=? WHERE id=?",
                          (term_start, term_end, now_str(), oid))
                DB._add_career(c, oid, '任职', r['position'], r['department'], term_start, term_end,
                               f'上级批量调整任期：{old_ts}~{old_te} → {term_start}~{term_end}')
                DB._new_agreement(c, oid, r['position'], r['department'], term_start, term_end)
                done += 1
            conn.commit()
            if done:
                DB.log(op_type, op_id, op_name, '批量调整任期',
                       f'共调整 {done} 名官员任期至 {term_start}~{term_end}'
                       + (f'，跳过（总统/副总统）：{",".join(skipped)}' if skipped else ''))
            tail = f'，跳过（总统/副总统）：{",".join(skipped)}' if skipped else ''
            return True, f'已批量调整 {done} 名官员任期至 {term_start} 至 {term_end}{tail}，' \
                         f'相关官员下次登录需重新签署协议', done, skipped
        finally:
            conn.close()

    @staticmethod
    def renewal_approver(official):
        """连任申请的审核上级：与离职审批同一套判定（总统→副总统；高级→总统；其他→本部门部长）。"""
        return DB.resignation_approver(official)

    @staticmethod
    def _term_length_days(ts, te):
        try:
            d1 = datetime.strptime((ts or '')[:10], '%Y-%m-%d')
            d2 = datetime.strptime((te or '')[:10], '%Y-%m-%d')
            return max(1, (d2 - d1).days)
        except Exception:
            return 365

    @staticmethod
    def _extend_term_end(te, days):
        try:
            d = datetime.strptime((te or '')[:10], '%Y-%m-%d') + timedelta(days=days)
            return d.strftime('%Y-%m-%d')
        except Exception:
            return te

    @staticmethod
    def _extend_term_years(te, years):
        """任期顺延 years 年（按公历日期，2月29日顺延至2月28日）。"""
        try:
            d = datetime.strptime((te or '')[:10], '%Y-%m-%d')
            try:
                nd = d.replace(year=d.year + years)
            except ValueError:
                nd = d.replace(year=d.year + years, day=28)
            return nd.strftime('%Y-%m-%d')
        except Exception:
            return te

    @staticmethod
    def submit_renewal(official, method='手动'):
        """官员提交连任申请（总统/副总统除外）。连任规则：一般连任至本次卸任日的 2 年后。
        返回 (ok, msg, rid)。"""
        pos = (official.get('position') or '').strip()
        if pos in ('总统', '副总统'):
            return False, '总统/副总统由大选产生，不适用连任申请', None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            r = c.execute("SELECT * FROM officials WHERE id=? AND status='在职'",
                          (official['id'],)).fetchone()
            if not r:
                return False, '仅在职官员可提交连任申请', None
            te = (r['term_end'] or '').strip()
            if not te:
                return False, '当前职务无固定任期，无需连任', None
            pend = c.execute("SELECT COUNT(*) FROM renewal_applications WHERE official_id=? AND status IN ('待审核','自动提交')",
                             (official['id'],)).fetchone()[0]
            if pend:
                return False, '已有待审核的连任申请，请等待审核结果', None
            ts = (r['term_start'] or '')[:10]
            # 连任规则：一般连任至本次卸任日的 2 年后
            new_te = DB._extend_term_years(te, 2)
            now = now_str()
            c.execute('''INSERT INTO renewal_applications (official_id,official_name,position,department,
                         old_term_start,old_term_end,new_term_end,status,submit_method,submitted_at)
                         VALUES (?,?,?,?,?,?,?, '待审核', ?, ?)''',
                      (r['id'], r['name'], r['position'], r['department'], ts, te[:10], new_te,
                       method, now))
            rid = c.lastrowid
            conn.commit()
            DB.log('official', r['id'], r['name'], '连任申请',
                   f'提交连任申请（当前任期至{te[:10]}，拟延至{new_te}）')
            return True, f'连任申请已提交，等待上级审核（拟延任至 {new_te}）', rid
        finally:
            conn.close()

    @staticmethod
    def my_renewals(official_id):
        conn = DB.get_conn(GOV_DB)
        try:
            rows = conn.execute("SELECT * FROM renewal_applications WHERE official_id=? ORDER BY id DESC",
                                (official_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def pending_renewals(reviewer):
        """返回当前审核人应审的全部待审核连任申请（与离职审批同口径）。"""
        conn = DB.get_conn(GOV_DB)
        try:
            rows = conn.execute("SELECT * FROM renewal_applications WHERE status IN ('待审核','自动提交') ORDER BY id").fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            off = {'id': r['official_id'], 'name': r['official_name'],
                   'position': r['position'], 'department': r['department']}
            ap = DB.renewal_approver(off)
            if ap and ap['id'] == reviewer['id']:
                out.append(dict(r))
        return out

    @staticmethod
    def review_renewal(rid, reviewer, approve, note=''):
        """上级审核连任申请：同意→任期顺延（term_end 延至 new_term_end）+ 档案“连任”履历 + 新协议。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            r = c.execute("SELECT * FROM renewal_applications WHERE id=?", (rid,)).fetchone()
            if not r:
                return False, '申请不存在'
            if r['status'] not in ('待审核', '自动提交'):
                return False, '该申请已被处理'
            now = now_str()
            if approve:
                c.execute("UPDATE officials SET term_end=?, last_update=? WHERE id=?",
                          (r['new_term_end'], now, r['official_id']))
                DB._add_career(c, r['official_id'], '连任', r['position'], r['department'],
                               r['old_term_start'], r['new_term_end'],
                               f'连任{("手动" if r["submit_method"]=="手动" else "自动")}申请获上级通过，'
                               f'任期由 {r["old_term_start"]}~{r["old_term_end"]} 延至 {r["new_term_end"]}')
                # 连任后任期改变 → 生成新协议待签署
                off = c.execute("SELECT * FROM officials WHERE id=?", (r['official_id'],)).fetchone()
                if off:
                    DB._new_agreement(c, off['id'], off['position'], off['department'],
                                      off['term_start'], off['term_end'])
                c.execute("UPDATE renewal_applications SET status='已通过', reviewed_by=?, reviewed_at=?, review_note=? WHERE id=?",
                          (reviewer['name'], now, note or '', rid))
                conn.commit()
                DB.log('official', reviewer['id'], reviewer['name'], '连任审核',
                       f'通过 {r["official_name"]} 的连任申请（延至 {r["new_term_end"]}）')
                return True, f'已通过 {r["official_name"]} 的连任申请，任期延至 {r["new_term_end"]}'
            else:
                c.execute("UPDATE renewal_applications SET status='已驳回', reviewed_by=?, reviewed_at=?, review_note=? WHERE id=?",
                          (reviewer['name'], now, note or '', rid))
                conn.commit()
                DB.log('official', reviewer['id'], reviewer['name'], '连任审核',
                       f'驳回 {r["official_name"]} 的连任申请')
                return True, f'已驳回 {r["official_name"]} 的连任申请'
        finally:
            conn.close()

    @staticmethod
    def set_auto_renewal(official_id, on):
        conn = DB.get_conn(GOV_DB)
        try:
            conn.execute("UPDATE officials SET auto_renewal=? WHERE id=?", (1 if on else 0, official_id))
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def career_tick():
        """任期自动化调度（登录时与周期轮询调用）：
        ① 任期届满自动卸任（非总统/副总统，term_end < 今日）；
        ② 任期结束前 10 天仍未提交连任且自动提交开关开启 → 系统自动提交（连任至本次卸任日的 2 年后）；
        ③ 卸任前 3 天仍无人受理 → 系统自动通过。
        返回动作消息列表（空列表=无动作）。"""
        today = datetime.now().strftime('%Y-%m-%d')
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        msgs = []
        try:
            # ① 自动卸任
            due = c.execute("SELECT * FROM officials WHERE status='在职' AND position NOT IN ('总统','副总统') "
                            "AND term_end IS NOT NULL AND term_end!='' AND term_end < ?", (today,)).fetchall()
            for o in due:
                c.execute("UPDATE officials SET status='已卸任', last_update=? WHERE id=?", (now_str(), o['id']))
                DB._add_career(c, o['id'], '卸任', o['position'], o['department'],
                               (o['term_start'] or '')[:10], (o['term_end'] or '')[:10],
                               f'任期届满（{(o["term_start"] or "")[:10]} 至 {(o["term_end"] or "")[:10]}）自动卸任')
                DB._log_raw(c, 'system', 0, '系统', '自动卸任', f'{o["name"]}任期届满自动卸任')
                msgs.append(f'🔔 {o["name"]} 任期届满（{(o["term_end"] or "")[:10]}），已自动卸任')
                DB.send_message('official', o['id'], '任期届满通知',
                                f'您担任 {o["department"]}{o["position"]} 的任期已于'
                                f' {(o["term_end"] or "")[:10]} 届满，系统已自动卸任，感谢您的履职。')
            conn.commit()
            # ② 自动提交连任（结束前 10 天）：连任至本次卸任日的 2 年后
            deadline10 = (datetime.now() + timedelta(days=10)).strftime('%Y-%m-%d')
            cands = c.execute("SELECT * FROM officials WHERE status='在职' AND position NOT IN ('总统','副总统') "
                              "AND auto_renewal=1 AND term_end IS NOT NULL AND term_end!='' "
                              "AND term_end>=? AND term_end<=?",
                              (today, deadline10)).fetchall()
            for o in cands:
                has = c.execute("SELECT COUNT(*) FROM renewal_applications WHERE official_id=? "
                                "AND status IN ('待审核','自动提交','已通过')", (o['id'],)).fetchone()[0]
                if has:
                    continue
                te = (o['term_end'] or '')[:10]
                new_te = DB._extend_term_years(te, 2)
                c.execute('''INSERT INTO renewal_applications (official_id,official_name,position,department,
                             old_term_start,old_term_end,new_term_end,status,submit_method,submitted_at)
                             VALUES (?,?,?,?,?,?,?, '自动提交', '自动', ?)''',
                          (o['id'], o['name'], o['position'], o['department'], ts, te, new_te, now_str()))
                DB._log_raw(c, 'system', 0, '系统', '自动提交连任', f'{o["name"]}临近卸任未提交连任，系统已自动提交')
                msgs.append(f'📮 {o["name"]} 任期结束前 10 天未提交连任申请，系统已自动提交')
                DB.send_message('official', o['id'], '连任申请已自动提交',
                                f'您的任期将于 {(o["term_end"] or "")[:10]} 结束，因临近卸任尚未提交连任申请，'
                                f'系统已为您自动提交（拟延任至 {new_te}）。如需关闭自动提交，请在“我的信息”中关闭。')
            conn.commit()
            # ③ 自动通过（卸任前 3 天无人受理）
            deadline3 = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
            pend = c.execute("SELECT * FROM renewal_applications WHERE status IN ('待审核','自动提交') "
                             "AND old_term_end<=? ", (deadline3,)).fetchall()
            for r in pend:
                c.execute("UPDATE officials SET term_end=?, last_update=? WHERE id=?",
                          (r['new_term_end'], now_str(), r['official_id']))
                DB._add_career(c, r['official_id'], '连任', r['position'], r['department'],
                               r['old_term_start'], r['new_term_end'],
                               f'连任申请在卸任前3天无人受理，系统自动通过（延至 {r["new_term_end"]}）')
                off = c.execute("SELECT * FROM officials WHERE id=?", (r['official_id'],)).fetchone()
                if off:
                    DB._new_agreement(c, off['id'], off['position'], off['department'],
                                      off['term_start'], off['term_end'])
                c.execute("UPDATE renewal_applications SET status='自动通过', reviewed_by='系统', "
                          "reviewed_at=?, review_note='卸任前3天无人受理，系统自动通过' WHERE id=?",
                          (now_str(), r['id']))
                DB._log_raw(c, 'system', 0, '系统', '自动通过连任',
                            f'{r["official_name"]}连任申请在卸任前3天无人受理，自动通过')
                msgs.append(f'✅ {r["official_name"]} 的连任申请卸任前 3 天无人受理，系统已自动通过')
                DB.send_message('official', r['official_id'], '连任申请自动通过',
                                f'您的连任申请（延任至 {r["new_term_end"]}）因卸任前 3 天无人受理，'
                                f'系统已自动通过。请在下一次登录时重新签署任职协议。')
            conn.commit()
        finally:
            conn.close()
        return msgs

    # ---------- 合同管理 ----------
    @staticmethod
    def create_contract(issuer, title, content, official_ids):
        """上级发起合同：对单个/多个官员。返回 (ok, msg, cid)。"""
        if not title.strip():
            return False, '请填写合同标题', None
        if not content.strip():
            return False, '请填写合同内容', None
        oids = [int(x) for x in official_ids if str(x).isdigit()]
        if not oids:
            return False, '请选择至少一位签署官员', None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        try:
            now = now_str()
            c.execute("INSERT INTO gov_contracts (title,content,issuer_id,issuer_name,created_at) VALUES (?,?,?,?,?)",
                      (title.strip(), content.strip(), issuer['id'], issuer['name'], now))
            cid = c.lastrowid
            for oid in oids:
                nm = c.execute("SELECT name FROM officials WHERE id=?", (oid,)).fetchone()
                c.execute("INSERT INTO contract_targets (contract_id,official_id,official_name,status,created_at) "
                          "VALUES (?,?,?, '待签署', ?)",
                          (cid, oid, nm['name'] if nm else str(oid), now))
            conn.commit()
            DB.log('official', issuer['id'], issuer['name'], '发起合同',
                   f'发起合同《{title.strip()}》，涉及 {len(oids)} 位官员')
            return True, f'合同已发起，共 {len(oids)} 位官员待签署', cid
        finally:
            conn.close()

    @staticmethod
    def contracts_for_issuer(issuer_id):
        conn = DB.get_conn(GOV_DB)
        try:
            rows = conn.execute("SELECT * FROM gov_contracts WHERE issuer_id=? ORDER BY id DESC",
                                (issuer_id,)).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            c2 = DB.get_conn(GOV_DB)
            try:
                targets = c2.execute("SELECT * FROM contract_targets WHERE contract_id=? ORDER BY id",
                                     (r['id'],)).fetchall()
            finally:
                c2.close()
            d['targets'] = [dict(t) for t in targets]
            signed = sum(1 for t in d['targets'] if t['status'] == '已签署')
            d['signed_count'] = signed
            d['total_count'] = len(d['targets'])
            out.append(d)
        return out

    @staticmethod
    def contracts_for_official(official_id):
        conn = DB.get_conn(GOV_DB)
        try:
            rows = conn.execute("SELECT * FROM contract_targets WHERE official_id=? ORDER BY id DESC",
                                (official_id,)).fetchall()
            out = []
            for t in rows:
                c2 = conn.execute("SELECT * FROM gov_contracts WHERE id=?", (t['contract_id'],)).fetchone()
                d = dict(t)
                d['contract'] = dict(c2) if c2 else {}
                out.append(d)
            return out
        finally:
            conn.close()

    @staticmethod
    def sign_contract(target_id, official_id, agree):
        """官员签署/拒绝合同。返回 (ok, msg)。"""
        conn = DB.get_conn(GOV_DB)
        try:
            t = conn.execute("SELECT * FROM contract_targets WHERE id=? AND official_id=?",
                             (target_id, official_id)).fetchone()
            if not t:
                return False, '合同不存在或未指定给您'
            if t['status'] != '待签署':
                return False, '该合同已处理'
            now = now_str()
            conn.execute("UPDATE contract_targets SET status=?, signed_at=? WHERE id=?",
                         ('已签署' if agree else '已拒绝', now, target_id))
            conn.commit()
            c = conn.execute("SELECT * FROM gov_contracts WHERE id=?", (t['contract_id'],)).fetchone()
            DB.log('official', official_id, t['official_name'], '签署合同',
                   f'{"签署" if agree else "拒绝"}合同《{c["title"] if c else ""}》')
            return True, ('合同已签署' if agree else '已拒绝该合同')
        finally:
            conn.close()

    @staticmethod
    def official_tenure(official):
        """计算某官员的任期信息（官员名录/详情展示用）。
        总统/副总统：任期默认 4 年（就职日起算），换届自动停止；
        连任时系统按「就职日基础上再 +4 年」写入 term_end（2031 连任→2035-01-20）。
        无当选记录的现任总统/副总统（临时）：按任命日起 4 年过渡。
        其他职务：任期由上级任命确定（term_start/term_end），任期届满自动卸任；
        无任期记录的旧数据按“任命制（无固定任期）”展示。"""
        pos = (official.get('position') or '').strip()
        name = official.get('name') or ''
        oid = official.get('id')
        ad = (official.get('appointed_date') or '')[:10]
        base = {'kind': '任命制（无固定任期）', 'term_start': '', 'term_end': '',
                'term_text': '任命制 · 随任命调整', 'resign_date': '—', 'terms': 0,
                'note': '非选任职务，无固定任期；卸任日随上级调整/离职审批确定'}
        if pos not in ('总统', '副总统'):
            ts = (official.get('term_start') or '').strip()
            te = (official.get('term_end') or '').strip()
            if ts or te:
                return {'kind': '任期制（上级任命确定）', 'term_start': ts, 'term_end': te,
                        'term_text': f'{ts} 至 {te}' if ts and te else (ts or te or '—'),
                        'resign_date': te or '—', 'terms': 0,
                        'note': '由上级任命确定任期，任期届满自动卸任；'
                                '结束前 30 天可在“我的信息”提交连任申请（总统/副总统除外）'}
            return base
        is_pres = pos == '总统'
        role_col = 'winner_id' if is_pres else 'vp_id'
        name_col = 'winner_name' if is_pres else 'vp_name'
        conn = DB.get_conn(GOV_DB)
        e = conn.execute(f"SELECT * FROM elections WHERE status='已结束' AND ({name_col}=? OR {role_col}=?) "
                         f"ORDER BY id DESC LIMIT 1", (name, oid)).fetchone()
        terms = conn.execute(f"SELECT COUNT(*) FROM elections WHERE status='已结束' AND ({name_col}=? OR {role_col}=?)",
                             (name, oid)).fetchone()[0]
        if e and (e['term_start'] or e['term_end']):
            ts = (e['term_start'] or '')[:10]
            te = (e['term_end'] or '')[:10]
            note = ('本届连任：在就职日基础上再 +4 年；换届自动停止' if terms > 1
                    else '任期默认 4 年；换届自动停止，连任在就职日基础上再 +4 年')
            conn.close()
            return {'kind': '4 年固定任期', 'term_start': ts, 'term_end': te,
                    'term_text': f'{ts} 至 {te}' if ts and te else '—',
                    'resign_date': te or '—', 'terms': terms, 'note': note}
        # 无本人当选记录：先看是否已有完成换届（当选者为他人），有则按「换届自动停止」处理
        e_other = conn.execute(
            f"SELECT * FROM elections WHERE status='已结束' AND {name_col} IS NOT NULL "
            f"AND {name_col}!='' ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        if e_other and (e_other['term_start'] or ''):
            stop = (e_other['term_start'] or '')[:10]
            elected = e_other[name_col]
            e_ts = (e_other['term_start'] or '')[:10]
            e_te = (e_other['term_end'] or '')[:10]
            base['kind'] = '过渡期' + ('总统' if is_pres else '副总统') + '（换届自动停止）'
            base.update(term_start=ad or '', term_end=stop,
                        term_text=(f'{ad} 至 {stop}（换届停止）' if ad else f'至 {stop}（换届停止）'),
                        resign_date=stop,
                        note=f'未当选本届职务，换届自动停止，于就职交接日（{stop}）卸任；'
                             f'本届当选{"总统" if is_pres else "副总统"}：{elected}（任期 {e_ts} 至 {e_te}）')
            return base
        # 大选尚未进行（现任临时总统/副总统）：自任命日起 4 年过渡
        base['kind'] = '临时任期（自任命日起 4 年）'
        if ad:
            try:
                _d = datetime.strptime(ad, '%Y-%m-%d')
                try:
                    _end = _d.replace(year=_d.year + 4)
                except ValueError:  # 2-29 边界
                    _end = _d.replace(year=_d.year + 4, day=28)
                end = _end.strftime('%Y-%m-%d')
            except Exception:
                end = ''
            base.update(term_start=ad, term_end=end,
                        term_text=f'{ad} 至 {end}' if end else '—',
                        resign_date=end or '—',
                        note='临时' + ('总统' if is_pres else '副总统') + '：大选尚未进行，任期自任命日起按 4 年计算，'
                             '至下次大选就职日/换届自动停止')
        return base

    @staticmethod
    def official_directory():
        """官员名录：全部官员 + 任期信息，按职位 sort_order 排序（总统→副总统→…→办事员）。"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute('''SELECT o.*, IFNULL(p.sort_order, 999) so
                               FROM officials o LEFT JOIN positions p ON p.name = o.position
                               ORDER BY so ASC, o.id ASC''').fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            out.append((d, DB.official_tenure(d)))
        return out

    @staticmethod
    def set_official_status(oid, new_status, action_name, op_type, op_id, op_name):
        """统一状态变更入口：总统被撤职时自动触发副总统继任（沿用原总统剩余任期）。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        r = c.execute("SELECT position, name FROM officials WHERE id=?", (oid,)).fetchone()
        is_pres = bool(r and r['position'] == '总统')
        pres_term_end = None
        if is_pres:
            e_rec = c.execute("SELECT term_end FROM elections WHERE status='已结束' AND (winner_id=? OR winner_name=?) "
                              "ORDER BY id DESC LIMIT 1", (oid, r['name'])).fetchone()
            if e_rec:
                pres_term_end = e_rec['term_end']
        c.execute("UPDATE officials SET status=?, last_update=? WHERE id=?", (new_status, now_str(), oid))
        succ = None
        if is_pres and new_status == '已撤职':
            succ = DB._auto_succession(c, '总统被撤职', '撤职', pres_term_end)
        conn.commit()
        conn.close()
        DB.log(op_type, op_id, op_name, action_name, f'对官员(职务:{r["position"] if r else "?"})执行{action_name}')
        return True, f'官员已{action_name}' + (f'，{succ} 自动接任总统' if succ else '')

    @staticmethod
    def delete_official_by_id(oid, op_id, op_name):
        """统一删除入口：删除在职总统时自动触发副总统继任（沿用原总统剩余任期）。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        r = c.execute("SELECT name, position FROM officials WHERE id=?", (oid,)).fetchone()
        if not r:
            conn.close()
            return False, '官员不存在'
        is_pres = r['position'] == '总统'
        pres_term_end = None
        if is_pres:
            e_rec = c.execute("SELECT term_end FROM elections WHERE status='已结束' AND (winner_id=? OR winner_name=?) "
                              "ORDER BY id DESC LIMIT 1", (oid, r['name'])).fetchone()
            if e_rec:
                pres_term_end = e_rec['term_end']
        c.execute("DELETE FROM officials WHERE id=?", (oid,))
        succ = None
        if is_pres:
            succ = DB._auto_succession(c, '总统被删除', '删除官员', pres_term_end)
        conn.commit()
        conn.close()
        DB.log('admin', op_id, op_name, '删除官员', f'删除官员{r["name"]}')
        return True, f'官员 {r["name"]} 已删除' + (f'，{succ} 自动接任总统' if succ else '')

    @staticmethod
    def resignation_approver(official):
        """返回离职申请的审批上级（dict 或 None）：
        总统→副总统；副总统/国务卿/部长/局长/署长→总统；其他→本部门部长，无部长则总统。"""
        conn = DB.get_conn(GOV_DB)
        try:
            pos = official.get('position') or ''
            pres = conn.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
            if pos == '总统':
                vp = conn.execute("SELECT * FROM officials WHERE position='副总统' AND status='在职' "
                                  "ORDER BY id LIMIT 1").fetchone()
                return dict(vp) if vp else None
            if pos in ('国务卿', '副总统') or '部长' in pos or '局长' in pos or '署长' in pos:
                return dict(pres) if pres else None
            dept = official.get('department') or ''
            m = conn.execute("SELECT * FROM officials WHERE status='在职' AND department=? "
                             "AND position LIKE '%部长%' AND position NOT LIKE '副%' "
                             "ORDER BY id LIMIT 1", (dept,)).fetchone()
            if m:
                return dict(m)
            return dict(pres) if pres else None
        finally:
            conn.close()

    @staticmethod
    def submit_resignation(official, reason):
        """在职官员提交离职申请（同一时间仅一条待审核）。"""
        if not reason or not reason.strip():
            return False, '请填写离职原因', None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        r = c.execute("SELECT * FROM officials WHERE id=? AND status='在职'", (official['id'],)).fetchone()
        if not r:
            conn.close()
            return False, '仅在职官员可提交离职申请', None
        pending = c.execute("SELECT COUNT(*) FROM resignations WHERE official_id=? AND status='待审核'",
                            (official['id'],)).fetchone()[0]
        if pending:
            conn.close()
            return False, '已有待审核的离职申请，请等待审核结果', None
        now = now_str()
        c.execute('''INSERT INTO resignations (official_id,official_name,department,position,reason,status,created_at)
                     VALUES (?,?,?,?,?, '待审核', ?)''',
                  (official['id'], official['name'], r['department'], r['position'], reason.strip(), now))
        rid = c.lastrowid
        conn.commit()
        conn.close()
        DB.log('official', official['id'], official['name'], '离职申请', f'提交离职申请（{reason.strip()[:40]}）')
        return True, '离职申请已提交，等待上级审核', rid

    @staticmethod
    def my_resignations(official_id):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM resignations WHERE official_id=? ORDER BY id DESC",
                            (official_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def pending_resignations(reviewer):
        """返回当前审核人应审的全部待审核离职申请。"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM resignations WHERE status='待审核' ORDER BY id").fetchall()
        conn.close()
        out = []
        for r in rows:
            off = {'id': r['official_id'], 'name': r['official_name'],
                   'position': r['position'], 'department': r['department']}
            ap = DB.resignation_approver(off)
            if ap and ap['id'] == reviewer['id']:
                out.append(dict(r))
        return out

    @staticmethod
    def review_resignation(rid, reviewer, approve, note=''):
        """上级审核离职申请：同意→办理离职（总统离职自动副总统继任）；驳回→退回。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        r = c.execute("SELECT * FROM resignations WHERE id=?", (rid,)).fetchone()
        if not r:
            conn.close()
            return False, '离职申请不存在'
        if r['status'] != '待审核':
            conn.close()
            return False, '该申请已被处理'
        now = now_str()
        if approve:
            off = c.execute("SELECT * FROM officials WHERE id=?", (r['official_id'],)).fetchone()
            is_pres = bool(off and off['position'] == '总统')
            pres_term_end = None
            if is_pres and off:
                e_rec = c.execute("SELECT term_end FROM elections WHERE status='已结束' AND (winner_id=? OR winner_name=?) "
                                  "ORDER BY id DESC LIMIT 1", (off['id'], off['name'])).fetchone()
                if e_rec:
                    pres_term_end = e_rec['term_end']
            c.execute("UPDATE officials SET status='已离职', last_update=? WHERE id=?", (now, r['official_id']))
            succ = None
            if is_pres and off:
                succ = DB._auto_succession(c, '总统辞职', '批准离职', pres_term_end)
            c.execute("UPDATE resignations SET status='已同意', reviewer_id=?, reviewer_name=?, "
                      "review_note=?, reviewed_at=? WHERE id=?",
                      (reviewer['id'], reviewer['name'], note.strip(), now, rid))
            conn.commit()
            conn.close()
            DB.log(reviewer.get('type', 'official'), reviewer['id'], reviewer['name'], '离职审批',
                   f'同意《{r["official_name"]}》离职申请')
            return True, f'已同意 {r["official_name"]} 离职' + (f'，{succ} 自动接任总统' if succ else '')
        c.execute("UPDATE resignations SET status='已驳回', reviewer_id=?, reviewer_name=?, "
                  "review_note=?, reviewed_at=? WHERE id=?",
                  (reviewer['id'], reviewer['name'], note.strip(), now, rid))
        conn.commit()
        conn.close()
        DB.log(reviewer.get('type', 'official'), reviewer['id'], reviewer['name'], '离职审批',
               f'驳回《{r["official_name"]}》离职申请（{note.strip()[:40]}）')
        return True, f'已驳回 {r["official_name"]} 的离职申请'

    @staticmethod
    def auto_regular_election():
        """宪法换届：自2026年起每隔4年、每年10月23日由系统自动开启一轮总统大选（幂等：同轮只开一次）。
        总统被弹劾/离职等中途空缺走补选，不占用本机制。"""
        dt = datetime.now()
        if not (dt.month == 10 and dt.day == 23 and (dt.year - 2026) % 4 == 0):
            return False, '未到自动大选日（10月23日）', None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        year = dt.year
        done = c.execute("SELECT COUNT(*) FROM elections WHERE election_type='regular' "
                         "AND created_at >= ?", (f'{year}-10-23',)).fetchone()[0]
        if done > 0:
            conn.close()
            return False, '本轮换届大选已自动开启', None
        active = c.execute("SELECT COUNT(*) FROM elections WHERE status IN ('候选人登记','投票中')").fetchone()[0]
        if active:
            conn.close()
            return False, '存在进行中的大选，待其结束后再开启换届大选', None
        round_no = c.execute("SELECT COUNT(*) FROM elections WHERE election_type='regular' AND status='已结束'").fetchone()[0] + 1
        now = now_str()
        c.execute("INSERT INTO elections (title,status,started_by,started_by_name,created_at,election_type,reason) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (f'第{round_no}轮总统大选', '候选人登记', 'system', '系统·宪法换届', now, 'regular', '宪法规定四年一届·自动开启'))
        eid = c.lastrowid
        conn.commit()
        DB._log_raw(c, 'system', 0, '系统', '自动开启大选',
                    f'第{round_no}轮总统大选自动开启（{dt.strftime("%Y-%m-%d")}），进入候选人登记')
        conn.close()
        return True, f'第{round_no}轮总统大选已自动开启，进入候选人登记阶段', eid

    @staticmethod
    def initiate_impeachment(target_id, target_name, reason, initiator_id, initiator_name, is_admin=False):
        """弹劾现任总统：由在职议员或管理员发起；同一时间仅一条进行中。"""
        if not reason or not reason.strip():
            return False, '请填写弹劾理由', None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        pres = c.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
        if not pres:
            conn.close()
            return False, '当前没有在职总统可弹劾', None
        if pres['id'] != int(target_id or 0):
            conn.close()
            return False, '只能弹劾现任总统', None
        pending = c.execute("SELECT COUNT(*) FROM impeachments WHERE status='进行中'").fetchone()[0]
        if pending:
            conn.close()
            return False, '已有弹劾案进行中，请等待表决结果', None
        if not is_admin:
            init = c.execute("SELECT * FROM officials WHERE id=? AND status='在职'", (initiator_id,)).fetchone()
            if not init or init['position'] not in ('参议员', '众议员', '参议院议长', '众议院议长'):
                conn.close()
                return False, '只有在职议员或管理员可以发起弹劾', None
        now = now_str()
        c.execute('''INSERT INTO impeachments (target_id,target_name,reason,initiator_id,initiator_name,
                     status,created_at) VALUES (?,?,?,?,?, '进行中', ?)''',
                  (target_id, target_name, reason, initiator_id, initiator_name, now))
        imp_id = c.lastrowid
        conn.commit()
        conn.close()
        return True, '弹劾案已提交议会表决：同意票达在职议员 2/3 即通过，总统自动撤职并交接', imp_id

    @staticmethod
    def list_impeachments(status=None):
        conn = DB.get_conn(GOV_DB)
        if status:
            rows = conn.execute("SELECT * FROM impeachments WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM impeachments ORDER BY id DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def cast_impeachment_vote(imp_id, official_id, official_name, vote):
        """议员对弹劾案投票（一人一票）。"""
        if vote not in ('赞成', '反对', '弃权'):
            return False, '投票选项无效'
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        imp = c.execute("SELECT * FROM impeachments WHERE id=?", (imp_id,)).fetchone()
        if not imp:
            conn.close()
            return False, '弹劾案不存在'
        if imp['status'] != '进行中':
            conn.close()
            return False, '该弹劾案已结束表决'
        off = c.execute("SELECT * FROM officials WHERE id=? AND status='在职'", (official_id,)).fetchone()
        if not off or off['position'] not in ('参议员', '众议员', '参议院议长', '众议院议长'):
            conn.close()
            return False, '只有在职议员可以参与弹劾表决'
        try:
            c.execute('''INSERT INTO impeachment_votes (impeachment_id,official_id,official_name,vote,voted_at)
                         VALUES (?,?,?,?,?)''', (imp_id, official_id, official_name, vote, now_str()))
            conn.commit()
            conn.close()
            return True, '表决成功'
        except sqlite3.IntegrityError:
            conn.close()
            return False, '您已投过票，每位议员一票'

    @staticmethod
    def impeachment_stats(imp_id):
        conn = DB.get_conn(GOV_DB)
        imp = c = None
        try:
            conn = DB.get_conn(GOV_DB)
            c = conn.cursor()
            imp = c.execute("SELECT * FROM impeachments WHERE id=?", (imp_id,)).fetchone()
            agg = {r[0]: r[1] for r in c.execute(
                "SELECT vote,COUNT(*) FROM impeachment_votes WHERE impeachment_id=? GROUP BY vote", (imp_id,)).fetchall()}
            voters = c.execute('''SELECT iv.*, o.department, o.position FROM impeachment_votes iv
                LEFT JOIN officials o ON o.id=iv.official_id WHERE iv.impeachment_id=? ORDER BY iv.id''',
                               (imp_id,)).fetchall()
            total = c.execute("SELECT COUNT(*) FROM officials WHERE status='在职' AND position IN "
                              "('参议员','众议员','参议院议长','众议院议长')").fetchone()[0]
            conn.close()
            return {'impeachment': dict(imp), 'yes': agg.get('赞成', 0), 'no': agg.get('反对', 0),
                    'abstain': agg.get('弃权', 0), 'voters': [dict(v) for v in voters], 'total_leg': total}
        except Exception:
            if conn:
                conn.close()
            return {'impeachment': dict(imp) if imp else {}, 'yes': 0, 'no': 0, 'abstain': 0,
                    'voters': [], 'total_leg': 0}

    @staticmethod
    def close_impeachment(imp_id):
        """结束弹劾表决：同意票 >= 在职议员总数 2/3（向上取整）→ 通过：
        总统自动撤职 → 副总统依宪继任；否则否决。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        imp = c.execute("SELECT * FROM impeachments WHERE id=?", (imp_id,)).fetchone()
        if not imp:
            conn.close()
            return False, '弹劾案不存在'
        if imp['status'] != '进行中':
            conn.close()
            return False, '该弹劾案已结束'
        agg = {r[0]: r[1] for r in c.execute(
            "SELECT vote,COUNT(*) FROM impeachment_votes WHERE impeachment_id=? GROUP BY vote", (imp_id,)).fetchall()}
        yes, no, abstain = agg.get('赞成', 0), agg.get('反对', 0), agg.get('弃权', 0)
        total = c.execute("SELECT COUNT(*) FROM officials WHERE status='在职' AND position IN "
                          "('参议员','众议员','参议院议长','众议院议长')").fetchone()[0]
        need = -(-total * 2 // 3) if total > 0 else 0  # ceil(在职议员数*2/3)
        now = now_str()
        if total == 0:
            c.execute("UPDATE impeachments SET status='未通过', yes_count=?, no_count=?, abstain_count=?, "
                      "reviewed_at=? WHERE id=?", (yes, no, abstain, now, imp_id))
            conn.commit()
            conn.close()
            return False, '没有在职议员，弹劾无法表决（驳回）'
        if yes >= need:
            pres = c.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
            pres_term_end = None
            if pres and pres['id'] == imp['target_id']:
                e_rec = c.execute("SELECT term_end FROM elections WHERE status='已结束' AND (winner_id=? OR winner_name=?) "
                                  "ORDER BY id DESC LIMIT 1", (pres['id'], pres['name'])).fetchone()
                if e_rec:
                    pres_term_end = e_rec['term_end']
                c.execute("UPDATE officials SET status='已撤职', last_update=? WHERE id=?", (now, pres['id']))
                DB._log_raw(c, 'system', pres['id'], pres['name'], '弹劾撤职',
                            f'议会表决通过，总统{pres["name"]}被弹劾撤职（同意 {yes}/{need}）')
            c.execute("UPDATE impeachments SET status='已通过', yes_count=?, no_count=?, abstain_count=?, "
                      "reviewed_at=? WHERE id=?", (yes, no, abstain, now, imp_id))
            conn.commit()
            succ = DB._auto_succession(c, '总统被弹劾撤职', '弹劾撤职', pres_term_end)
            conn.commit()
            conn.close()
            msg = f'弹劾通过！同意 {yes}/{need}（在职议员 {total}，需 2/3={need}）'
            if succ:
                msg += f'，{succ} 已自动接任总统（任期四年）'
            return True, msg
        c.execute("UPDATE impeachments SET status='未通过', yes_count=?, no_count=?, abstain_count=?, "
                  "reviewed_at=? WHERE id=?", (yes, no, abstain, now, imp_id))
        conn.commit()
        conn.close()
        return False, f'弹劾未通过（同意 {yes}，需 {need}/{total} 赞成）'

    @staticmethod
    def remove_impeachment_vote(imp_id, official_id):
        """管理员撤销某议员的弹劾投票（改票用）。"""
        conn = DB.get_conn(GOV_DB)
        conn.execute("DELETE FROM impeachment_votes WHERE impeachment_id=? AND official_id=?", (imp_id, official_id))
        conn.commit()
        conn.close()

    # ================= 总统竞选自荐申请（官员/公民） =================
    @staticmethod
    def submit_candidacy(user, platform, party_id=0, party_name='', partner_id_number='', partner_name=''):
        """官员/公民自荐参选总统：填写申请书与副总统搭档（户籍核对）→ 提交后台审核。
        时间窗：大选开启（10-23）后、10月28日前可申请（登记期间即可投票）。"""
        if not platform or not platform.strip():
            return False, '请填写竞选申请书', None
        # 硬校验：剥夺政治权利执行期内不得参选总统（本人与搭档都要查）
        for _idn, _who in ((user.get('id_number'), '你'), ((partner_id_number or '').strip(), '副总统搭档')):
            ban = DB.political_ban(_idn)
            if ban.get('banned'):
                return False, ('%s正处于剥夺政治权利执行期（判决 %s，至 %s 期满），'
                               '期满前不得参选总统' % (_who, ban.get('case_number') or '—', ban.get('until'))), None
        pidn = (partner_id_number or '').strip()
        pname = (partner_name or '').strip()
        if not pidn or not pname:
            return False, '必须选择副总统搭档（填写搭档身份证号码与姓名）', None
        resident = DB.verify_household(pidn, pname)
        if not resident:
            return False, f'副总统搭档「{pname}」在户籍系统中不存在，请核对身份证号码与姓名', None
        if resident['id_number'] == user['id_number']:
            return False, '副总统搭档不能是候选人本人', None
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        now = now_str()
        elec = c.execute("SELECT * FROM elections WHERE status IN ('候选人登记','投票中') AND election_type='regular' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
        if not elec:
            conn.close()
            return False, '当前没有可申请的大选（大选开启后至10月28日前可申请）', None
        yr = int(str(elec['created_at'])[:4])
        if now > DB.candidacy_deadline_from(yr):
            conn.close()
            return False, '参选登记已截止（仅限10月28日前申请参选）', None
        dup_p = c.execute("SELECT name FROM candidacy_applications WHERE id_number=? AND status='待审核'",
                          (user['id_number'],)).fetchone()
        if dup_p:
            conn.close()
            return False, '您已有待审核的参选申请，请等待审核结果', None
        constitution = c.execute("SELECT * FROM constitution WHERE status='生效中' ORDER BY version DESC LIMIT 1").fetchone()
        if constitution:
            oid = user.get('id') or 0
            if not oid:
                off = c.execute("SELECT id FROM officials WHERE id_number=?", (user['id_number'],)).fetchone()
                if off:
                    oid = off['id']
            if oid:
                cnt = DB.president_term_count(oid)
                if cnt >= 2:
                    conn.close()
                    return False, f'{user["name"]} 已当选总统 {cnt} 届，依据宪法累计不得超过两届，不得再参选', None
        c.execute('''INSERT INTO candidacy_applications (applicant_type,applicant_id,id_number,name,
                     party_id,party_name,platform,partner_id_number,partner_name,status,election_id,created_at)
                     VALUES (?,?,?,?,?,?,?,?,?, '待审核', ?, ?)''',
                  ('official' if user['type'] == 'official' else 'citizen',
                   user.get('id') or 0, user['id_number'], user['name'],
                   party_id, party_name, platform, pidn, pname, elec['id'], now))
        aid = c.lastrowid
        conn.commit()
        conn.close()
        return True, '参选申请已提交，等待管理后台审核', aid

    @staticmethod
    def list_candidacy_applications(status=None):
        conn = DB.get_conn(GOV_DB)
        if status:
            rows = conn.execute("SELECT * FROM candidacy_applications WHERE status=? ORDER BY id DESC",
                                (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM candidacy_applications ORDER BY id DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def my_candidacy_applications(user):
        """某官员/公民自己的参选申请记录"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM candidacy_applications WHERE id_number=? ORDER BY id DESC",
                            (user['id_number'],)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def review_candidacy(req_id, approve, actor_name=''):
        """后台审核参选申请：通过→（公民自动建档为总统候选人）加入当前大选候选名单；否则拒绝。"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        r = c.execute("SELECT * FROM candidacy_applications WHERE id=?", (req_id,)).fetchone()
        if not r or r['status'] != '待审核':
            conn.close()
            return False, '申请不存在或已处理'
        now = now_str()
        if not approve:
            c.execute("UPDATE candidacy_applications SET status='已拒绝', reviewed_at=? WHERE id=?", (now, req_id))
            conn.commit()
            conn.close()
            return True, f'已拒绝 {r["name"]} 的参选申请'
        elec = c.execute("SELECT * FROM elections WHERE id=? AND status IN ('候选人登记','投票中')",
                         (r['election_id'],)).fetchone()
        if not elec:
            conn.close()
            return False, '关联大选已不在候选人登记/投票阶段，无法通过（请先撤销该申请）'
        if elec['election_type'] == 'regular':
            yr = int(str(elec['created_at'])[:4])
            if now > DB.candidacy_deadline_from(yr):
                conn.close()
                return False, '参选登记已截止（仅限10月28日前申请参选），无法通过'
        oid = r['applicant_id']
        if not oid:
            off = c.execute("SELECT id FROM officials WHERE id_number=?", (r['id_number'],)).fetchone()
            if off:
                oid = off['id']
        if oid:
            cnt = DB.president_term_count(oid)
            if cnt >= 2:
                conn.close()
                return False, f'{r["name"]} 已当选总统 {cnt} 届，依据宪法不得超过两届，不能通过'
        if r['applicant_type'] == 'citizen' or not oid:
            off = c.execute("SELECT id FROM officials WHERE id_number=?", (r['id_number'],)).fetchone()
            if off:
                oid = off['id']
            else:
                c.execute('''INSERT INTO officials (id_number,name,gender,department,position,rank,status,
                             appointed_date,last_update) VALUES (?,?,?, '竞选登记', '总统候选人', '公民', '在职', ?, ?)''',
                          (r['id_number'], r['name'], '未知', now, now))
                oid = c.lastrowid
            c.execute("UPDATE candidacy_applications SET applicant_id=? WHERE id=?", (oid, req_id))
        try:
            c.execute('''INSERT INTO election_candidates (election_id,party_id,party_name,official_id,official_name,
                         platform,partner_id_number,partner_name,created_at)
                         VALUES (?,?,?,?,?,?,?,?,?)''',
                      (r['election_id'], r['party_id'], r['party_name'], oid, r['name'], r['platform'],
                       r['partner_id_number'] or '', r['partner_name'] or '', now))
        except sqlite3.IntegrityError:
            conn.close()
            return False, '该申请人已在本次大选候选名单中'
        c.execute("UPDATE candidacy_applications SET status='已通过', reviewed_at=? WHERE id=?", (now, req_id))
        DB._log_raw(c, 'system', oid, r['name'], '参选审核通过',
                    f'{r["name"]}（{r["applicant_type"]}）参选申请通过，已加入大选候选名单（搭档 {r["partner_name"] or "无"}）')
        conn.commit()
        conn.close()
        okv, msgv, _ = DB.auto_start_voting_if_ready(r['election_id'])
        return True, f'已通过 {r["name"]}（搭档 {r["partner_name"] or "无"}）参选申请并加入候选名单' + \
                     ('，' + msgv if okv else '')

    # ---- 权力目录查询（GUI 用） ----
    @staticmethod
    def power_catalog():
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM power_catalog ORDER BY sort_order").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def set_position_power(position_id, power_key, mode):
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO position_powers (position_id,power_key,mode) VALUES (?,?,?)''',
                  (position_id, power_key, mode))
        conn.commit()
        conn.close()

    @staticmethod
    def clear_position_powers(position_id):
        """恢复该职务全部 44 项为随职级。"""
        conn = DB.get_conn(GOV_DB)
        conn.execute("DELETE FROM position_powers WHERE position_id=?", (position_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def position_power_map(position_id):
        """该职务 44 项权力点覆盖：pkey -> 'default'/'allow'/'deny'。"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT power_key,mode FROM position_powers WHERE position_id=?", (position_id,)).fetchall()
        conn.close()
        return {r['power_key']: r['mode'] for r in rows}

    @staticmethod
    def set_official_power(official_id, power_key, mode):
        """个人权力覆盖：allow/deny/remove（移除=回到职务+职级默认）"""
        conn = DB.get_conn(GOV_DB)
        c = conn.cursor()
        if mode == 'remove':
            c.execute("DELETE FROM official_perm_overrides WHERE official_id=? AND perm_key=?",
                      (official_id, power_key))
        else:
            c.execute('''INSERT OR REPLACE INTO official_perm_overrides (official_id,perm_key,mode) VALUES (?,?,?)''',
                      (official_id, power_key, mode))
        conn.commit()
        conn.close()

    @staticmethod
    def official_power_overrides(official_id):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT perm_key,mode FROM official_perm_overrides WHERE official_id=?",
                            (official_id,)).fetchall()
        conn.close()
        return {r['perm_key']: r['mode'] for r in rows}

    # ---- 总统行政令 ----
    @staticmethod
    def create_executive_order(title, content, official, effective_mode, effective_time=''):
        """总统签署/发布行政令。
        effective_mode: '立即生效' / '定时生效'（定时需精确到分钟，格式 YYYY-MM-DD HH:MM）。
        返回 (ok, msg)；ok=True 时 msg 为令号。"""
        now = now_str()
        if effective_mode == '定时生效':
            try:
                datetime.strptime(effective_time, '%Y-%m-%d %H:%M')
            except Exception:
                return False, '定时生效时间格式不正确，需精确到分钟（如 2026-10-16 14:30）'
            if effective_time <= now[:16]:
                return False, '定时生效时间必须晚于当前时间'
        else:
            effective_time = now[:16]
        conn = DB.get_conn(GOV_DB)
        n = conn.execute("SELECT COUNT(*) FROM executive_orders").fetchone()[0] + 1
        order_no = f"总统令〔{datetime.now():%Y}〕{n}号"
        conn.execute('''INSERT INTO executive_orders
            (order_no,title,content,publisher_id,publisher_name,publisher_position,
             effective_mode,effective_time,is_withdrawn,created_at)
            VALUES (?,?,?,?,?,?,?,?,0,?)''',
            (order_no, title, content, official['id'], official['name'],
             official.get('position') or '总统', effective_mode, effective_time, now))
        conn.commit()
        conn.close()
        DB.log('official', official['id'], official['name'], '签署行政令',
               f"发布《{title}》（{order_no}），生效方式：{effective_mode}，生效时间：{effective_time}")
        return True, order_no

    @staticmethod
    def withdraw_executive_order(order_id, official, manage=False):
        """撤回行政令。
        默认仅发布人（总统本人）可撤回；manage=True 时（行政令管理）现任总统可撤回
        任意总统（含前任总统）发布的行政令。返回 (ok, msg)。"""
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT * FROM executive_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            conn.close()
            return False, '行政令不存在'
        if r['is_withdrawn']:
            conn.close()
            return False, '该行政令已撤回，不能重复撤回'
        if not manage and r['publisher_id'] != official['id']:
            conn.close()
            return False, '只能撤回本人发布的行政令'
        now = now_str()
        conn.execute("UPDATE executive_orders SET is_withdrawn=1, withdrawn_at=?, withdrawn_by=? WHERE id=?",
                     (now, official['name'], order_id))
        conn.commit()
        conn.close()
        DB.log('official', official['id'], official['name'],
               '撤回行政令' + ('（行政令管理）' if manage else ''),
               f"撤回《{r['title']}》（{r['order_no']}），发布人：{r['publisher_name']}")
        return True, ''

    @staticmethod
    def _exec_order_status(r):
        """行政令当前展示状态：已撤回 / 已生效 / 待生效（按当前时间动态判定）。"""
        if r['is_withdrawn']:
            return '已撤回'
        try:
            if datetime.strptime(r['effective_time'], '%Y-%m-%d %H:%M') <= datetime.now():
                return '已生效'
        except Exception:
            pass
        return '待生效'

    @staticmethod
    def list_executive_orders():
        """全部行政令（含已撤回），附带计算后的展示状态。"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM executive_orders ORDER BY id DESC").fetchall()
        conn.close()
        return [{**dict(r), 'status': DB._exec_order_status(r)} for r in rows]

    @staticmethod
    def get_executive_order(order_id):
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT * FROM executive_orders WHERE id=?", (order_id,)).fetchone()
        conn.close()
        if not r:
            return None
        return {**dict(r), 'status': DB._exec_order_status(r)}

    # ========== 总统：保释罪犯（对接监狱管理系统·商业版，数据位于 household_registration.db 的 pz_ 表） ==========
    @staticmethod
    def search_prisoners(keyword):
        """按身份证号 / 囚号 / 姓名搜索监狱管理系统中的在押罪犯。返回字典列表。"""
        conn = DB.get_conn(HOUSEHOLD_DB)
        try:
            kw = (keyword or '').strip()
            sql = ("SELECT id, inmate_no, id_number, name, gender, case_name, case_type, "
                   "sentence_months, expected_release_date, entry_date, status "
                   "FROM pz_inmates WHERE status='在押'")
            p = []
            if kw:
                sql += " AND (inmate_no LIKE ? OR id_number LIKE ? OR name LIKE ?)"
                p = [f'%{kw}%'] * 3
            sql += " ORDER BY id DESC LIMIT 200"
            rows = conn.execute(sql, p).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    @staticmethod
    def bail_prisoners(inmate_ids, reason, bail_date, operator):
        """总统批量保释：将保释信息写入监狱管理系统（商业版）。
        更新在押状态为“取保候审”→ 写入出监记录（pz_release_records）→ 监狱操作日志 →
        户籍状态恢复“正常”。返回 (ok, msg, done_count, names)。"""
        reason = (reason or '').strip()
        try:
            datetime.strptime(bail_date, '%Y-%m-%d')
        except Exception:
            return False, '保释日期格式不正确（YYYY-MM-DD）', 0, []
        if not reason:
            return False, '请填写保释原因', 0, []
        if not inmate_ids:
            return False, '请选择要保释的罪犯', 0, []
        conn = DB.get_conn(HOUSEHOLD_DB)
        c = conn.cursor()
        now = now_str()
        done, names = 0, []
        try:
            for pid in inmate_ids:
                inm = c.execute("SELECT * FROM pz_inmates WHERE id=?", (pid,)).fetchone()
                if not inm or inm['status'] != '在押':
                    continue
                c.execute("UPDATE pz_inmates SET status='取保候审', cell_id=0, bed_no='', updated_at=? WHERE id=?",
                          (now, pid))
                c.execute('''INSERT INTO pz_release_records
                    (inmate_id, inmate_no, id_number, name, release_type, release_date, reason,
                     settle_type, settle_amount, bank_txn_id, belongings_returned, operator, remark, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (pid, inm['inmate_no'], inm['id_number'], inm['name'], '取保候审', bail_date,
                           f'总统保释：{reason}', '', 0, None, 0, f'{operator}（总统）', '', now))
                c.execute('''INSERT INTO pz_op_logs (user_id, username, real_name, module, action, detail, log_time)
                    VALUES (?,?,?,?,?,?,?)''',
                          (0, 'gov', operator, '出监办理', '取保候审',
                           f'总统保释 {inm["name"]}（{inm["inmate_no"]}），保释日期 {bail_date}，原因：{reason}', now))
                try:
                    c.execute("UPDATE residents SET status='正常', last_update=? WHERE id_number=?",
                              (now, (inm['id_number'] or '').strip().upper()))
                except Exception:
                    pass
                done += 1
                names.append(inm['name'])
            conn.commit()
            if not done:
                return False, '所选罪犯均不在押或已不存在，未执行保释', 0, []
            return True, f'已向监狱管理系统发送保释信息，共 {done} 名罪犯获保释（{",".join(names)}）', done, names
        finally:
            conn.close()

    @staticmethod
    def list_bail_records(limit=50):
        """读取监狱管理系统中的保释记录（出监记录中“取保候审”类型，按时间倒序）。"""
        conn = DB.get_conn(HOUSEHOLD_DB)
        try:
            rows = conn.execute('''SELECT id, inmate_no, id_number, name, release_type, release_date,
                     reason, operator, created_at
                FROM pz_release_records WHERE release_type='取保候审' ORDER BY id DESC LIMIT ?''',
                                (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    @staticmethod
    def create_bail_executive_order(title, content, official, effective_time):
        """发布“保释罪犯”行政令：生效日期=保释日期（允许当天或已过日期，不校验“必须晚于当前”）。
        令号与普通行政令连续编号。返回 (ok, msg/order_no)。"""
        conn = DB.get_conn(GOV_DB)
        try:
            n = conn.execute("SELECT COUNT(*) FROM executive_orders").fetchone()[0] + 1
            order_no = f"总统令〔{datetime.now():%Y}〕{n}号"
            now = now_str()
            conn.execute('''INSERT INTO executive_orders
                (order_no,title,content,publisher_id,publisher_name,publisher_position,
                 effective_mode,effective_time,is_withdrawn,created_at)
                VALUES (?,?,?,?,?,?,?,?,0,?)''',
                          (order_no, title, content, official['id'], official['name'],
                           official.get('position') or '总统', '定时生效', effective_time, now))
            conn.commit()
            DB.log('official', official['id'], official['name'], '签署行政令',
                   f"发布《{title}》（{order_no}），保释生效时间：{effective_time}")
            return True, order_no
        finally:
            conn.close()


# ========== 工具函数 ==========
def make_flat_button(parent, text, command, color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HOVER,
                     width=15, height=2, font_size=11):
    """创建银行系统风格的扁平按钮"""
    btn = tk.Button(parent, text=text, command=command, width=width, height=height,
                    bg=color, fg='white', font=("Arial", font_size), relief=tk.FLAT, cursor='hand2')
    btn.bind("<Enter>", lambda e: btn.config(bg=hover_color))
    btn.bind("<Leave>", lambda e: btn.config(bg=color))
    return btn


def make_groove_entry(parent, width=30, show='', font_size=11):
    """创建银行系统风格的输入框"""
    return tk.Entry(parent, width=width, font=("Arial", font_size), bd=2, relief=tk.GROOVE, show=show)


def make_page_title(parent, text, color=COLOR_TITLE):
    """创建页面标题"""
    return tk.Label(parent, text=text, font=("Arial", 18, "bold"), bg=COLOR_CONTENT_BG, fg=color)


def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def gen_no(prefix):
    """按计数生成唯一编号，如 DB202608270001 / YY202608270001 / 蛋政发〔2026〕1号"""
    try:
        conn = DB.get_conn(GOV_DB)
        if prefix == 'DB':
            n = conn.execute("SELECT COUNT(*) FROM service_cases").fetchone()[0] + 1
            no = f"DB{datetime.now():%Y%m%d}{n:04d}"
        elif prefix == 'YY':
            n = conn.execute("SELECT COUNT(*) FROM service_appointments").fetchone()[0] + 1
            no = f"YY{datetime.now():%Y%m%d}{n:04d}"
        elif prefix == 'GW':
            n = conn.execute("SELECT COUNT(*) FROM official_docs").fetchone()[0] + 1
            no = f"蛋政发〔{datetime.now():%Y}〕{n}号"
        else:
            no = f"{prefix}{datetime.now():%Y%m%d%H%M%S}{n:04d}"
        conn.close()
        return no
    except:
        return f"{prefix}{datetime.now():%Y%m%d%H%M%S}"


def make_tree(parent, columns, widths, height=12, anchor=tk.CENTER):
    """创建带滚动条的标准表格，返回 (frame, tree)"""
    frame = tk.Frame(parent, bg=COLOR_CONTENT_BG)
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
    for c in columns:
        tree.heading(c, text=c)
        tree.column(c, width=widths.get(c, 100), anchor=anchor)
    sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(fill=tk.BOTH, expand=True)
    return frame, tree


def make_foldable_content(parent, lines, max_lines=6):
    """宪法/草案条文显示：内容行数不超过 max_lines 时直接展示；
    超过则默认折叠为前 max_lines 行 + “展开查看全文”按钮，
    点击弹出带滑动条的新弹窗展示全文（只读）。"""
    lines = [ln.strip() for ln in (lines or []) if ln and ln.strip()]
    if not lines:
        return
    bg = parent.cget('bg')

    def open_full():
        top = parent.winfo_toplevel()
        win = tk.Toplevel(top)
        win.title('📄 查看全文')
        win.geometry('760x520+220+100')
        win.transient(top)
        win.grab_set()
        tk.Label(win, text='📄 查看全文', font=("Arial", 13, "bold")).pack(pady=(10, 4))
        tk.Label(win, text=f'共 {len(lines)} 条 · 可滚动查看', font=("Arial", 9), fg='#888').pack()
        fr = tk.Frame(win)
        fr.pack(fill='both', expand=True, padx=14, pady=6)
        txt = tk.Text(fr, font=("Arial", 10), wrap='word', relief=tk.GROOVE, bd=1, bg='#fdfdfd')
        sb = ttk.Scrollbar(fr, orient='vertical', command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        txt.pack(side='left', fill='both', expand=True)
        for ln in lines:
            txt.insert('end', ln + '\n')
        txt.configure(state='disabled')
        make_flat_button(win, '关闭', win.destroy, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(pady=8)

    if len(lines) <= max_lines:
        for ln in lines:
            tk.Label(parent, text=ln, font=("Arial", 10), bg=bg, fg='#444',
                     anchor='w', justify='left', wraplength=1050).pack(anchor='w')
        return
    for ln in lines[:max_lines]:
        tk.Label(parent, text=ln, font=("Arial", 10), bg=bg, fg='#444',
                 anchor='w', justify='left', wraplength=1050).pack(anchor='w')
    more = tk.Frame(parent, bg=bg)
    more.pack(anchor='w', pady=(3, 1))
    tk.Label(more, text=f'…… 内容较长（共 {len(lines)} 条），已折叠显示前 {max_lines} 条：',
             font=("Arial", 9), bg=bg, fg=COLOR_TEXT_GRAY).pack(side='left')
    make_flat_button(more, '📄 展开查看全文', open_full, COLOR_PURPLE, COLOR_PURPLE,
                     width=14, height=1, font_size=10).pack(side='left', padx=6)


# ========== 登录窗口 ==========
class LoginWindow:
    def __init__(self, root):
        self.root = root
        self.root.title('蛋国政务管理系统 - 登录')
        self.root.geometry('520x480')
        self.root.resizable(False, False)
        self.root.configure(bg='white')
        self.current_user = None
        self._build_ui()

    def _build_ui(self):
        login_frame = tk.Frame(self.root, bg='white')
        login_frame.pack(expand=True)

        tk.Label(login_frame, text="蛋国政务服务一体化平台", font=("Arial", 22, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=(25, 5))
        tk.Label(login_frame, text="蛋国政务管理系统 · 登录", font=("Arial", 12),
                 bg='white', fg=COLOR_TEXT_GRAY).pack(pady=(0, 15))

        # 角色选择
        role_frame = tk.Frame(login_frame, bg='white')
        role_frame.pack(pady=5)
        tk.Label(role_frame, text="登录身份：", font=("Arial", 11), bg='white').pack(side='left', padx=5)
        self.role_var = tk.StringVar(value='citizen')
        for text, val in [('普通民众', 'citizen'), ('政府官员', 'official'), ('临时账号', 'temp'), ('系统管理员', 'admin')]:
            tk.Radiobutton(role_frame, text=text, variable=self.role_var, value=val,
                           font=("Arial", 10), bg='white', command=self._toggle_fields).pack(side='left', padx=5)

        # 表单
        form_frame = tk.Frame(login_frame, bg='white')
        form_frame.pack(pady=15)

        # 民众/官员身份字段（直接持有引用，避免 grid_slaves 崩溃）
        self.id_label = tk.Label(form_frame, text="身份证号：", font=("Arial", 12), bg='white')
        self.id_label.grid(row=0, column=0, padx=15, pady=12, sticky=tk.E)
        self.id_entry = make_groove_entry(form_frame, width=30)
        self.id_entry.grid(row=0, column=1, padx=15, pady=12)
        self.name_label = tk.Label(form_frame, text="姓　名：", font=("Arial", 12), bg='white')
        self.name_label.grid(row=1, column=0, padx=15, pady=12, sticky=tk.E)
        self.name_entry = make_groove_entry(form_frame, width=30)
        self.name_entry.grid(row=1, column=1, padx=15, pady=12)

        # 管理员字段
        self.admin_user_label = tk.Label(form_frame, text="用户名：", font=("Arial", 12), bg='white')
        self.admin_user = make_groove_entry(form_frame, width=30)
        self.admin_pwd_label = tk.Label(form_frame, text="密　码：", font=("Arial", 12), bg='white')
        self.admin_pwd = make_groove_entry(form_frame, width=30, show='*')

        # 临时账号字段
        self.temp_acc_label = tk.Label(form_frame, text="临时账号：", font=("Arial", 12), bg='white')
        self.temp_acc = make_groove_entry(form_frame, width=30)
        self.temp_pwd_label = tk.Label(form_frame, text="临时密码：", font=("Arial", 12), bg='white')
        self.temp_pwd = make_groove_entry(form_frame, width=30, show='*')

        # 登录按钮
        login_btn = tk.Button(login_frame, text="登 录", command=self._login,
                              width=20, height=2, bg=COLOR_PRIMARY, fg='white',
                              font=("Arial", 12), relief=tk.FLAT, cursor='hand2')
        login_btn.pack(pady=15)
        login_btn.bind("<Enter>", lambda e: login_btn.config(bg=COLOR_PRIMARY_HOVER))
        login_btn.bind("<Leave>", lambda e: login_btn.config(bg=COLOR_PRIMARY))

        tk.Label(login_frame, text="民众/官员：身份证号 + 姓名（户籍系统实名核验）",
                 font=("Arial", 9), bg='white', fg=COLOR_TEXT_GRAY).pack(pady=5)

        apply_btn = tk.Button(login_frame, text="📝 申请蛋国国籍（外国人入籍通道）",
                              command=self._open_nationality_apply,
                              bg=COLOR_SUCCESS, fg='white', font=("Arial", 10),
                              relief=tk.FLAT, cursor='hand2', padx=10, pady=5)
        apply_btn.pack(pady=(8, 0))

        self._toggle_fields()

    def _open_nationality_apply(self):
        """打开入籍申请弹窗。"""
        win = tk.Toplevel(self.root)
        win.title("申请蛋国国籍")
        win.geometry("480x700")
        win.configure(bg='white')
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text="📝 蛋国国籍入籍申请", font=("Arial", 14, "bold"),
                 bg='white', fg=COLOR_PRIMARY).pack(pady=10)
        form = tk.Frame(win, bg='white')
        form.pack(fill='x', padx=25)
        fields = []
        for i, (label, key) in enumerate([
            ('姓名*', 'name'), ('原国籍*', 'orig'), ('出生日期(YYYY-MM-DD)*', 'birth'),
            ('性别', 'gender'), ('外国护照号', 'passport'), ('申请原因*', 'reason')]):
            tk.Label(form, text=label, bg='white', font=("Arial", 10)).grid(row=i, column=0, sticky='w', pady=4)
            if key == 'reason':
                e = tk.Text(form, width=32, height=4, font=("Arial", 10))
            else:
                e = make_groove_entry(form, width=32)
            e.grid(row=i, column=1, pady=4, padx=8)
            fields.append((key, e))
        fmap = dict(fields)

        # ---- 人脸识别（必须先通过，且不得与户籍库中任何人重复）----
        face_state = {'b64': ''}
        face_frame = tk.Frame(win, bg='white')
        face_frame.pack(fill='x', padx=25, pady=(8, 0))
        face_lbl = tk.Label(face_frame, text='👤 人脸识别：未完成（完成前不能提交申请）',
                            bg='white', fg=COLOR_DANGER, font=("Arial", 9),
                            wraplength=400, justify='left')
        face_lbl.pack(anchor='w')

        def do_face():
            ok_cv, err = DB.face_available()
            if not ok_cv:
                messagebox.showerror('缺少组件', '人脸识别需要 opencv-python：\n' + err, parent=win)
                return
            p = filedialog.askopenfilename(
                parent=win, title='选择本人正面免冠照片',
                filetypes=[('图片文件', '*.jpg *.jpeg *.png *.bmp *.webp'), ('所有文件', '*.*')])
            if not p:
                return
            try:
                with open(p, 'rb') as f:
                    raw = f.read()
            except Exception as e:
                messagebox.showerror('读取失败', str(e), parent=win)
                return
            res = DB.face_identify(raw)
            if res.get('matched'):
                face_state['b64'] = ''
                face_lbl.config(text='❌ 人脸重复：%s' % res.get('reason'), fg=COLOR_DANGER)
                messagebox.showerror('不能申请', res.get('reason') or '人脸重复', parent=win)
                return
            if not res.get('ok'):
                face_state['b64'] = ''
                face_lbl.config(text='❌ %s' % (res.get('reason') or '识别失败'), fg=COLOR_DANGER)
                messagebox.showerror('识别失败', res.get('reason') or '识别失败', parent=win)
                return
            face_state['b64'] = base64.b64encode(raw).decode('ascii')
            face_lbl.config(
                text='✅ 人脸识别通过（已与户籍库 %d 张人脸比对，无重复，最高相似度 %.2f）'
                     % (res.get('gallery', 0), res.get('score', 0.0)), fg=COLOR_SUCCESS)

        make_flat_button(face_frame, '👤 上传照片并识别人脸', do_face,
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER).pack(anchor='w', pady=4)

        def submit():
            name = fmap['name'].get().strip()
            orig = fmap['orig'].get().strip()
            birth = fmap['birth'].get().strip()
            gender = fmap['gender'].get().strip() or '未知'
            passport = fmap['passport'].get().strip()
            reason = fmap['reason'].get('1.0', 'end').strip()
            if not name or not orig or not birth or not reason:
                messagebox.showwarning("提示", "请填写带 * 的必填项", parent=win)
                return
            if not face_state['b64']:
                messagebox.showwarning("提示", "请先点「上传照片并识别人脸」完成人脸识别", parent=win)
                return
            ok, msg, acc, pw, app_id = DB.submit_nationality_app(
                name, orig, birth, gender, reason, passport, face_state['b64'])
            if not ok:
                messagebox.showerror("错误", msg, parent=win)
                return
            win.destroy()
            # 弹窗显示账号密码
            r = tk.Toplevel(self.root)
            r.title("申请已提交")
            r.geometry("420x400")
            r.configure(bg='white')
            r.transient(self.root)
            tk.Label(r, text="✅ 申请已提交，等待总统/副总统审核", font=("Arial", 12, "bold"),
                     bg='white', fg=COLOR_SUCCESS).pack(pady=15)
            tk.Label(r, text="请牢记以下临时账号密码（审核通过后用它登录查看结果）：",
                     bg='white', font=("Arial", 10)).pack()

            def copy_text(text):
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                self.root.update()

            row_acc = tk.Frame(r, bg='white')
            row_acc.pack(pady=6)
            tk.Label(row_acc, text=f"账号：{acc}", bg='white', font=("Consolas", 14, "bold"), fg=COLOR_PRIMARY).pack(side='left', padx=8)
            tk.Button(row_acc, text="📋 复制", command=lambda: copy_text(acc),
                      bg=COLOR_PRIMARY, fg='white', relief=tk.FLAT, padx=8, pady=2).pack(side='left')

            row_pw = tk.Frame(r, bg='white')
            row_pw.pack(pady=6)
            tk.Label(row_pw, text=f"密码：{pw}", bg='white', font=("Consolas", 14, "bold"), fg=COLOR_PRIMARY).pack(side='left', padx=8)
            tk.Button(row_pw, text="📋 复制", command=lambda: copy_text(pw),
                      bg=COLOR_PRIMARY, fg='white', relief=tk.FLAT, padx=8, pady=2).pack(side='left')

            tk.Button(r, text="📋 一键复制账号+密码", command=lambda: copy_text(f"账号：{acc}\n密码：{pw}"),
                      bg=COLOR_SUCCESS, fg='white', relief=tk.FLAT, padx=12, pady=4).pack(pady=8)
            tk.Label(r, text="登录方式：登录页选「临时账号」→ 输入账号密码",
                     bg='white', fg=COLOR_GRAY, font=("Arial", 9)).pack(pady=5)
            tk.Button(r, text="我已记录", command=r.destroy, bg=COLOR_GRAY, fg='white',
                      relief=tk.FLAT, padx=20, pady=5).pack(pady=5)

        tk.Button(win, text="提交申请", command=submit, bg=COLOR_SUCCESS, fg='white',
                  relief=tk.FLAT, padx=20, pady=5).pack(pady=12)

    def _toggle_fields(self):
        """切换登录字段（修复原版 grid_slaves 在 grid_remove 后返回空列表的崩溃）"""
        role = self.role_var.get()
        # 先全部隐藏
        for w in (self.id_label, self.id_entry, self.name_label, self.name_entry,
                  self.admin_user_label, self.admin_user, self.admin_pwd_label,
                  self.temp_acc_label, self.temp_acc, self.temp_pwd_label, self.temp_pwd):
            w.grid_remove()
        if role == 'admin':
            self.admin_user_label.grid(row=0, column=0, padx=15, pady=12, sticky=tk.E)
            self.admin_user.grid(row=0, column=1, padx=15, pady=12)
            self.admin_pwd_label.grid(row=1, column=0, padx=15, pady=12, sticky=tk.E)
            self.admin_pwd.grid(row=1, column=1, padx=15, pady=12)
        elif role == 'temp':
            self.temp_acc_label.grid(row=0, column=0, padx=15, pady=12, sticky=tk.E)
            self.temp_acc.grid(row=0, column=1, padx=15, pady=12)
            self.temp_pwd_label.grid(row=1, column=0, padx=15, pady=12, sticky=tk.E)
            self.temp_pwd.grid(row=1, column=1, padx=15, pady=12)
        else:
            self.id_label.grid(row=0, column=0, padx=15, pady=12, sticky=tk.E)
            self.id_entry.grid(row=0, column=1, padx=15, pady=12)
            self.name_label.grid(row=1, column=0, padx=15, pady=12, sticky=tk.E)
            self.name_entry.grid(row=1, column=1, padx=15, pady=12)

    def _login(self):
        role = self.role_var.get()
        if role == 'admin':
            username = self.admin_user.get().strip()
            password = self.admin_pwd.get().strip()
            if not username or not password:
                messagebox.showwarning('提示', '请输入用户名和密码')
                return
            conn = DB.get_conn(GOV_DB)
            row = conn.execute("SELECT * FROM admins WHERE username=? AND password=?",
                               (username, password)).fetchone()
            conn.close()
            if not row:
                messagebox.showerror('登录失败', '用户名或密码错误')
                return
            self.current_user = {'type': 'admin', 'id': row['id'], 'name': row['real_name'], 'username': row['username']}
            DB.log('admin', row['id'], row['real_name'], '登录', '管理员登录系统')
        elif role == 'temp':
            acc = self.temp_acc.get().strip()
            pw = self.temp_pwd.get().strip()
            row = DB.temp_login(acc, pw)
            if not row:
                messagebox.showerror('登录失败', '临时账号或密码错误，或该账号已被注销')
                return
            self.current_user = {'type': 'temp', 'app': row}
        else:
            id_number = self.id_entry.get().strip()
            name = self.name_entry.get().strip()
            if not id_number or not name:
                messagebox.showwarning('提示', '请输入身份证号和姓名')
                return
            resident = DB.verify_household(id_number, name)
            if not resident:
                messagebox.showerror('登录失败', '户籍系统未找到该身份信息，请核对身份证号和姓名')
                return
            if role == 'official':
                conn = DB.get_conn(GOV_DB)
                off = conn.execute("SELECT * FROM officials WHERE id_number=? AND status='在职'",
                                   (id_number,)).fetchone()
                conn.close()
                if not off:
                    messagebox.showerror('登录失败', '您不是在任政府官员，无法以官员身份登录')
                    return
                self.current_user = {'type': 'official', 'id': off['id'], 'id_number': id_number,
                                     'name': name, 'position': off['position'],
                                     'department': off['department'], 'rank': off['rank'],
                                     'status': off['status'],
                                     'term_start': off['term_start'] or '',
                                     'term_end': off['term_end'] or '',
                                     'auto_renewal': off['auto_renewal'] if off['auto_renewal'] is not None else 1}
                DB.log('official', off['id'], name, '登录', f'官员{name}登录系统')
            else:
                self.current_user = {'type': 'citizen', 'id_number': id_number, 'name': name,
                                     'gender': resident.get('gender', ''), 'address': resident.get('address', '')}
                DB.log('citizen', id_number, name, '登录', f'民众{name}登录系统')
        self.root.destroy()


# ========== 主应用 ==========
class GovApp:
    def __init__(self, user):
        self.user = user
        # 临时账号：补全通用字段，避免 dashboard 等处 KeyError
        if user.get('type') == 'temp':
            user.setdefault('id_number', '')
            user.setdefault('name', user.get('app', {}).get('applicant_name', '临时用户'))
        self.root = tk.Tk()
        if user['type'] == 'admin':
            role_text = "管理员"
            disp_name = user['name']
        elif user['type'] == 'official':
            role_text = "政府官员"
            disp_name = user['name']
        elif user['type'] == 'temp':
            role_text = "入籍申请临时账号"
            disp_name = user['app']['applicant_name']
        else:
            role_text = "普通民众"
            disp_name = user['name']
        self.root.title(f"蛋国政务管理系统 - {disp_name}（{role_text}）")
        self.root.geometry("1420x820")
        self.root.minsize(1150, 700)
        self._build_layout()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        # 全局异常可见化：按钮回调等运行时异常弹出提示框，避免"点了没反应"
        def _report_callback_exception(exc_type, exc_value, exc_tb):
            import traceback as _tb
            msg = ''.join(_tb.format_exception(exc_type, exc_value, exc_tb))
            try:
                messagebox.showerror('系统错误', f'操作过程中发生错误，请截图反馈：\n\n{msg}')
            except Exception:
                pass
        self.root.report_callback_exception = _report_callback_exception
        # 官员登录：先跑一次任期自动化调度（自动卸任/自动提交连任/自动通过），再检查待签署协议
        if user['type'] == 'official':
            try:
                DB.career_tick()
            except Exception:
                pass
            # 总统/副总统：若无待签协议，补建一条（首次当选欢迎签署）
            try:
                if (user.get('position') or '') in ('总统', '副总统'):
                    if not DB.pending_agreement(user['id']):
                        DB.ensure_presidential_agreement(user['id'])
            except Exception:
                pass
            self._agreement_blocked = bool(DB.pending_agreement(user['id']))
            self.root.after(500, self._check_agreement)
        # 第 4 批：登录后弹「总统发布的特别活动」，并向本机推送待更新的版本
        try:
            self.root.after(900, self._popup_special_events)
        except Exception:
            pass
        try:
            self.root.after(1100, self._popup_update)
        except Exception:
            pass
        # 数据库级身份校验：职位/账号必须与数据库一致，改代码抬权会被挡下
        try:
            _ok_actor, _why = DB.verify_actor(user)
            if not _ok_actor:
                messagebox.showwarning('身份校验未通过', _why)
        except Exception:
            pass

    # ========== 第 4 批：总统特别活动 ==========
    @staticmethod
    def _norm_dt(s):
        """把用户输入的时间统一成 YYYY-MM-DD HH:MM:SS；认不出就返回空串。"""
        s = (s or '').strip()
        if not s:
            return ''
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
                    '%Y/%m/%d %H:%M', '%Y/%m/%d', '%Y.%m.%d'):
            try:
                return datetime.strptime(s, fmt).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
        return ''

    def _popup_special_events(self):
        """登录后弹窗：总统发布的特别活动（只弹有效期内的）。"""
        try:
            if getattr(self, '_special_event_popped', False):
                return
            self._special_event_popped = True
            if self.user.get('type') == 'temp':
                return
            evs = [e for e in DB.list_special_events(only_published=True) if DB.event_active(e)]
            if not evs:
                return
            ev = evs[0]
            win = tk.Toplevel(self.root)
            win.title('总统发布的特别活动')
            win.geometry('580x440')
            win.configure(bg='white')
            tk.Label(win, text='🎉 总统发布的特别活动', font=('Arial', 18, 'bold'),
                     bg='white', fg=COLOR_WARNING).pack(pady=(16, 8))
            body = tk.Frame(win, bg='white')
            body.pack(fill='both', expand=True, padx=24)
            tk.Label(body, text=ev['title'], font=('Arial', 14, 'bold'), bg='white',
                     wraplength=500, justify='left').pack(anchor='w', pady=(0, 6))
            info = ['活动时间：%s ~ %s' % (ev.get('start_at') or '—', ev.get('end_at') or '不限期')]
            if ev.get('entry_url'):
                info.append('活动入口：%s' % ev['entry_url'])
            info.append('发布人：%s' % (ev.get('publisher') or '总统'))
            tk.Label(body, text='\n'.join(info), font=('Arial', 10), bg='white',
                     fg=COLOR_TEXT_GRAY, justify='left').pack(anchor='w')
            tk.Label(body, text='活动详情', font=('Arial', 11, 'bold'), bg='white').pack(anchor='w', pady=(10, 2))
            txt = tk.Text(body, height=7, font=('Arial', 11), wrap='word', bd=1, relief=tk.SOLID)
            txt.insert('1.0', ev.get('detail') or '（无详情）')
            txt.config(state='disabled')
            txt.pack(fill='both', expand=True)
            if len(evs) > 1:
                tk.Label(win, text='另有 %d 个进行中的活动，可在左侧「🎉 特别活动」里查看' % (len(evs) - 1),
                         font=('Arial', 9), bg='white', fg=COLOR_TEXT_GRAY).pack(pady=(6, 0))
            make_flat_button(win, '我知道了', win.destroy, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=12, height=1).pack(pady=12)
            win.transient(self.root)
        except Exception:
            pass

    def _open_entry_url(self, url):
        """本地版不联网：入口只复制到剪贴板，由用户自行打开。"""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            messagebox.showinfo('活动入口', '活动入口已复制到剪贴板（本机版不自动联网打开）：\n%s' % url)
        except Exception:
            messagebox.showinfo('活动入口', url)

    def _show_special_events_view(self):
        self._clear_main()
        make_page_title(self.main_frame, '🎉 特别活动').pack(pady=16)
        tk.Label(self.main_frame, text='总统发布的特别活动 · 双击某行查看详情',
                 font=('Arial', 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 8))
        frame, tree = make_tree(self.main_frame,
                                ['活动名称', '活动时间', '状态', '发布人', '活动入口'],
                                {'活动名称': 230, '活动时间': 235, '状态': 90,
                                 '发布人': 110, '活动入口': 270},
                                height=15)
        frame.pack(fill='both', expand=True, padx=25)
        rows = {}
        for ev in DB.list_special_events():
            if DB.event_active(ev):
                st = '进行中'
            elif (ev.get('status') or '') == '已撤回':
                st = '已撤回'
            elif (ev.get('status') or '') == '已发布':
                st = '已过期'
            else:
                st = ev.get('status') or ''
            iid = tree.insert('', 'end', values=(ev.get('title') or '', 
                                                 '%s ~ %s' % (ev.get('start_at') or '—', ev.get('end_at') or '不限期'),
                                                 st, ev.get('publisher') or '—', ev.get('entry_url') or '—'))
            rows[iid] = ev

        def open_detail(_e=None):
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选中一行活动')
                return
            self._special_event_detail_dialog(rows[sel[0]])

        tree.bind('<Double-1>', open_detail)
        make_flat_button(self.main_frame, '查看所选活动详情', open_detail,
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=18, height=1).pack(pady=10)

    def _special_event_detail_dialog(self, ev):
        win = tk.Toplevel(self.root)
        win.title('活动详情')
        win.geometry('620x520')
        win.configure(bg='white')
        tk.Label(win, text=ev.get('title') or '', font=('Arial', 16, 'bold'), bg='white',
                 wraplength=560, justify='left').pack(pady=(16, 6), padx=20)
        st = '进行中' if DB.event_active(ev) else (ev.get('status') or '已失效')
        head = '活动时间：%s ~ %s\n状态：%s    发布人：%s' % (
            ev.get('start_at') or '—', ev.get('end_at') or '不限期', st, ev.get('publisher') or '—')
        tk.Label(win, text=head, font=('Arial', 10), bg='white', fg=COLOR_TEXT_GRAY,
                 justify='left').pack(anchor='w', padx=20)
        if ev.get('entry_url'):
            ent = tk.Label(win, text='🔗 活动入口：%s' % ev['entry_url'], font=('Arial', 11, 'underline'),
                           bg='white', fg=COLOR_PRIMARY, cursor='hand2')
            ent.pack(anchor='w', padx=20, pady=(6, 0))
            ent.bind('<Button-1>', lambda _e: self._open_entry_url(ev['entry_url']))
        tk.Label(win, text='活动详情', font=('Arial', 11, 'bold'), bg='white').pack(anchor='w', padx=20, pady=(12, 2))
        txt = tk.Text(win, font=('Arial', 11), wrap='word', bd=1, relief=tk.SOLID)
        txt.insert('1.0', ev.get('detail') or '（无详情）')
        txt.config(state='disabled')
        txt.pack(fill='both', expand=True, padx=20, pady=(0, 6))
        _ph = self._img_from_b64(ev.get('image_b64'), 520, 220)
        if _ph:
            _lb = tk.Label(win, image=_ph, bg='white')
            _lb.image = _ph
            _lb.pack(pady=4)
            tk.Label(win, text='（活动图片）', font=('Arial', 9), bg='white',
                     fg=COLOR_TEXT_GRAY).pack()
        make_flat_button(win, '关闭', win.destroy, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(pady=8)
        win.transient(self.root)

    def _show_special_event_admin(self):
        self._clear_main()
        make_page_title(self.main_frame, '🎉 发布 / 管理特别活动').pack(pady=14)
        tk.Label(self.main_frame,
                 text='发布后，官员端与民众端下次进入首页会弹窗提醒「总统发布的特别活动」；过了结束时间自动失效。',
                 font=('Arial', 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 6))

        form = tk.LabelFrame(self.main_frame, text='发布新活动', font=('Arial', 12, 'bold'),
                             bg=COLOR_CONTENT_BG, padx=16, pady=8)
        form.pack(fill='x', padx=30, pady=4)
        r1 = tk.Frame(form, bg=COLOR_CONTENT_BG)
        r1.pack(fill='x', pady=3)
        tk.Label(r1, text='活动名称：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left')
        name_e = make_groove_entry(r1, width=46)
        name_e.pack(side='left', padx=4)
        r2 = tk.Frame(form, bg=COLOR_CONTENT_BG)
        r2.pack(fill='x', pady=3)
        tk.Label(r2, text='活动入口(可选)：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left')
        url_e = make_groove_entry(r2, width=56)
        url_e.pack(side='left', padx=4)
        r3 = tk.Frame(form, bg=COLOR_CONTENT_BG)
        r3.pack(fill='x', pady=3)
        tk.Label(r3, text='活动时间：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left')
        tk.Label(r3, text='开始', bg=COLOR_CONTENT_BG, font=('Arial', 10)).pack(side='left', padx=(4, 2))
        start_e = make_groove_entry(r3, width=17)
        start_e.pack(side='left', padx=2)
        tk.Label(r3, text='结束(过期失效)', bg=COLOR_CONTENT_BG, font=('Arial', 10)).pack(side='left', padx=(12, 2))
        end_e = make_groove_entry(r3, width=17)
        end_e.pack(side='left', padx=2)
        tk.Label(r3, text='格式 2026-12-31 18:00（开始留空=立即开始）', bg=COLOR_CONTENT_BG,
                 font=('Arial', 9), fg=COLOR_TEXT_GRAY).pack(side='left', padx=8)
        tk.Label(form, text='活动详情：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(anchor='w', pady=(4, 0))
        detail_e = scrolledtext.ScrolledText(form, height=4, font=('Arial', 11), wrap='word')
        detail_e.pack(fill='x', pady=3)
        img_state = {'b64': ''}
        ir = tk.Frame(form, bg=COLOR_CONTENT_BG)
        ir.pack(fill='x', pady=3)
        il = tk.Label(ir, text='活动图片（jpg/png，可选）：未上传', bg=COLOR_CONTENT_BG,
                      font=('Arial', 9), fg=COLOR_TEXT_GRAY)
        il.pack(side='left', padx=(0, 8))

        def _pick_ev_img():
            b64 = self._pick_image_b64(860)
            if b64:
                img_state['b64'] = b64
                il.config(text='图片已选择（保存后显示在活动详情里）', fg=COLOR_SUCCESS)

        make_flat_button(ir, '📎 选择图片', _pick_ev_img, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(side='left')

        def do_publish():
            title = name_e.get().strip()
            if not title:
                messagebox.showwarning('提示', '请填写活动名称')
                return
            end_at = self._norm_dt(end_e.get())
            if end_e.get().strip() and not end_at:
                messagebox.showwarning('提示', '结束时间格式不对，请按 2026-12-31 18:00 填写')
                return
            start_at = self._norm_dt(start_e.get())
            if start_e.get().strip() and not start_at:
                messagebox.showwarning('提示', '开始时间格式不对，请按 2026-12-31 18:00 填写')
                return
            ok, msg = DB.publish_special_event(title, url_e.get().strip(),
                                               detail_e.get('1.0', 'end').strip(), end_at,
                                               self.user.get('name') or '', '', img_state['b64'])
            messagebox.showinfo('成功' if ok else '提示', msg)
            if ok:
                self._show_special_event_admin()

        make_flat_button(form, '🎉 发布活动', do_publish, COLOR_WARNING, COLOR_WARNING,
                         width=14, height=1).pack(anchor='e', pady=6)

        list_f = tk.LabelFrame(self.main_frame, text='已发布活动（双击可编辑）', font=('Arial', 12, 'bold'),
                               bg=COLOR_CONTENT_BG, padx=10, pady=6)
        list_f.pack(fill='both', expand=True, padx=30, pady=6)
        frame, tree = make_tree(list_f, ['ID', '活动名称', '活动时间', '状态', '发布人'],
                                {'ID': 50, '活动名称': 250, '活动时间': 250, '状态': 90, '发布人': 120},
                                height=9)
        frame.pack(fill='both', expand=True)
        rows = {}
        for ev in DB.list_special_events():
            st = '进行中' if DB.event_active(ev) else (
                '已撤回' if (ev.get('status') or '') == '已撤回' else '已过期')
            iid = tree.insert('', 'end', values=(ev['id'], ev.get('title') or '',
                                                 '%s ~ %s' % (ev.get('start_at') or '—', ev.get('end_at') or '不限期'),
                                                 st, ev.get('publisher') or '—'))
            rows[iid] = ev

        def _sel():
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请先在列表里选中一条活动')
                return None
            return rows[s[0]]

        def do_edit(_e=None):
            ev = _sel()
            if ev:
                self._special_event_edit_dialog(ev)

        def do_withdraw():
            ev = _sel()
            if ev and messagebox.askyesno('确认', '撤回活动「%s」？各端将不再显示。' % ev.get('title')):
                ok, msg = DB.withdraw_special_event(ev['id'])
                messagebox.showinfo('提示', msg)
                self._show_special_event_admin()

        def do_republish():
            ev = _sel()
            if ev:
                ok, msg = DB.republish_special_event(ev['id'])
                messagebox.showinfo('提示', msg)
                self._show_special_event_admin()

        def do_delete():
            ev = _sel()
            if ev and messagebox.askyesno('确认', '彻底删除活动「%s」？删除后无法恢复。' % ev.get('title')):
                ok, msg = DB.delete_special_event(ev['id'])
                messagebox.showinfo('提示', msg)
                self._show_special_event_admin()

        tree.bind('<Double-1>', do_edit)
        bar = tk.Frame(list_f, bg=COLOR_CONTENT_BG)
        bar.pack(fill='x', pady=6)
        make_flat_button(bar, '✏️ 编辑并重发', do_edit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=14, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '↩️ 撤回', do_withdraw, COLOR_WARNING, COLOR_WARNING,
                         width=10, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '▶️ 重新发布', do_republish, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                         width=12, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '🗑️ 删除', do_delete, COLOR_DANGER, COLOR_DANGER_HOVER,
                         width=10, height=1).pack(side='left', padx=6)

    def _special_event_edit_dialog(self, ev):
        win = tk.Toplevel(self.root)
        win.title('编辑活动')
        win.geometry('620x480')
        win.configure(bg='white')
        tk.Label(win, text='编辑活动（保存后自动重新发布）', font=('Arial', 14, 'bold'),
                 bg='white').pack(pady=12)
        frm = tk.Frame(win, bg='white')
        frm.pack(fill='both', expand=True, padx=20)
        for lbl in ('活动名称', '活动入口(可选)'):
            r = tk.Frame(frm, bg='white')
            r.pack(fill='x', pady=3)
            tk.Label(r, text=lbl + '：', bg='white', font=('Arial', 11), width=13, anchor='w').pack(side='left')
        name_e = make_groove_entry(frm, width=50)
        name_e.insert(0, ev.get('title') or '')
        name_e.pack(anchor='w', padx=90, pady=(0, 2))
        url_e = make_groove_entry(frm, width=50)
        url_e.insert(0, ev.get('entry_url') or '')
        url_e.pack(anchor='w', padx=90, pady=(0, 6))
        r3 = tk.Frame(frm, bg='white')
        r3.pack(fill='x', pady=3)
        tk.Label(r3, text='开始/结束时间：', bg='white', font=('Arial', 11)).pack(side='left')
        start_e = make_groove_entry(r3, width=18)
        start_e.insert(0, ev.get('start_at') or '')
        start_e.pack(side='left', padx=3)
        end_e = make_groove_entry(r3, width=18)
        end_e.insert(0, ev.get('end_at') or '')
        end_e.pack(side='left', padx=3)
        tk.Label(frm, text='活动详情：', bg='white', font=('Arial', 11)).pack(anchor='w', pady=(6, 0))
        detail_e = scrolledtext.ScrolledText(frm, height=8, font=('Arial', 11), wrap='word')
        detail_e.insert('1.0', ev.get('detail') or '')
        detail_e.pack(fill='both', expand=True, pady=3)
        img_state = {'b64': ''}
        ir = tk.Frame(frm, bg='white')
        ir.pack(fill='x', pady=3)
        il = tk.Label(ir, text=('当前已有活动图片（不选新图则保留）' if ev.get('image_b64') else '未上传图片'),
                      bg='white', font=('Arial', 9), fg=COLOR_TEXT_GRAY)
        il.pack(side='left', padx=(0, 8))

        def _pick_ev_img():
            b64 = self._pick_image_b64(860)
            if b64:
                img_state['b64'] = b64
                il.config(text='新图片已选择（保存后替换）', fg=COLOR_SUCCESS)

        make_flat_button(ir, '📎 选择图片', _pick_ev_img, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(side='left')

        def save():
            title = name_e.get().strip()
            if not title:
                messagebox.showwarning('提示', '活动名称不能为空')
                return
            start_at = self._norm_dt(start_e.get())
            end_at = self._norm_dt(end_e.get())
            if start_e.get().strip() and not start_at:
                messagebox.showwarning('提示', '开始时间格式不对')
                return
            if end_e.get().strip() and not end_at:
                messagebox.showwarning('提示', '结束时间格式不对')
                return
            ok, msg = DB.update_special_event(ev['id'], title, url_e.get().strip(),
                                              detail_e.get('1.0', 'end').strip(), end_at, start_at,
                                              img_state['b64'])
            messagebox.showinfo('提示', msg)
            if ok:
                win.destroy()
                self._show_special_event_admin()

        make_flat_button(win, '💾 保存并重发', save, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                         width=16, height=1).pack(pady=12)
        win.transient(self.root)

    # ========== 第 4 批：系统更新（热更新） ==========
    def _popup_update(self):
        """登录后推送：已到更新时间、本机还没应用过的更新。"""
        try:
            if self.user.get('type') == 'temp':
                return
            key = DB.device_key()
            pend = DB.pending_updates(key)
            if not pend:
                return
            upd = pend[0]
            win = tk.Toplevel(self.root)
            win.title('系统更新推送')
            win.geometry('600x470')
            win.configure(bg='white')
            tk.Label(win, text='🔄 发现系统更新 v%s' % (upd.get('version') or ''), font=('Arial', 17, 'bold'),
                     bg='white', fg=COLOR_PRIMARY).pack(pady=(16, 6))
            tk.Label(win, text='更新时间：%s    发布人：%s' % (upd.get('publish_at') or '—',
                                                            upd.get('publisher') or '—'),
                     font=('Arial', 10), bg='white', fg=COLOR_TEXT_GRAY).pack()
            tk.Label(win, text='更新内容', font=('Arial', 11, 'bold'), bg='white').pack(anchor='w', padx=24, pady=(12, 2))
            txt = tk.Text(win, height=8, font=('Arial', 11), wrap='word', bd=1, relief=tk.SOLID)
            txt.insert('1.0', upd.get('note') or '（无说明）')
            txt.config(state='disabled')
            txt.pack(fill='both', expand=True, padx=24)
            tk.Label(win, text='同一台设备只需更新一次，更新后不会再重复提示。', font=('Arial', 9),
                     bg='white', fg=COLOR_TEXT_GRAY).pack(pady=(6, 0))

            def do_update():
                win.destroy()
                self._apply_update_ui(upd)

            def do_skip():
                DB.skip_update(upd['id'], key)
                win.destroy()
                messagebox.showinfo('已跳过', '之后可在左侧「🔄 系统更新」列表里随时手动更新。')

            btns = tk.Frame(win, bg='white')
            btns.pack(pady=12)
            make_flat_button(btns, '✅ 立即更新并重启', do_update, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                             width=18, height=1).pack(side='left', padx=8)
            make_flat_button(btns, '稍后再说', do_skip, COLOR_TEXT_GRAY, '#8a8a8f',
                             width=14, height=1).pack(side='left', padx=8)
            win.transient(self.root)
        except Exception:
            pass

    def _apply_update_ui(self, upd, ask=True):
        ver = upd.get('version') or ''
        if ask and not messagebox.askyesno(
                '确认更新',
                '即将把 v%s 的新代码写入程序文件，然后重启程序。\n'
                '写入前会先做语法校验，校验不通过或写入失败会自动回滚，不会弄坏现有程序。\n\n现在更新吗？' % ver):
            return False
        ok, msg = DB.apply_system_update(upd['id'], DB.device_key())
        if not ok:
            messagebox.showwarning('更新失败', msg)
            return False
        messagebox.showinfo('更新完成', msg)
        try:
            self.root.destroy()
        except Exception:
            pass
        DB.restart_app()
        return True

    def _show_update_admin(self):
        self._clear_main()
        make_page_title(self.main_frame, '🔄 系统更新管理').pack(pady=12)
        tk.Label(self.main_frame,
                 text='粘贴完整的新程序代码 + 版本号 + 更新时间 + 更新内容。到点后各端登录会收到推送；每台设备只更新一次。',
                 font=('Arial', 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 4))

        form = tk.LabelFrame(self.main_frame, text='发布更新', font=('Arial', 12, 'bold'),
                             bg=COLOR_CONTENT_BG, padx=14, pady=6)
        form.pack(fill='both', expand=True, padx=24, pady=4)
        r1 = tk.Frame(form, bg=COLOR_CONTENT_BG)
        r1.pack(fill='x', pady=3)
        tk.Label(r1, text='版本号：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left')
        ver_e = make_groove_entry(r1, width=18)
        ver_e.pack(side='left', padx=4)
        tk.Label(r1, text='更新时间：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left', padx=(14, 2))
        at_e = make_groove_entry(r1, width=20)
        at_e.insert(0, now_str())
        at_e.pack(side='left', padx=4)
        tk.Label(r1, text='格式 2026-12-31 18:00', bg=COLOR_CONTENT_BG,
                 font=('Arial', 9), fg=COLOR_TEXT_GRAY).pack(side='left', padx=6)
        tk.Label(form, text='更新内容：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(anchor='w', pady=(4, 0))
        note_e = scrolledtext.ScrolledText(form, height=3, font=('Arial', 11), wrap='word')
        note_e.pack(fill='x', pady=3)
        tk.Label(form, text='新代码（粘贴完整程序文件内容）：', bg=COLOR_CONTENT_BG,
                 font=('Arial', 11)).pack(anchor='w', pady=(4, 0))
        code_e = scrolledtext.ScrolledText(form, height=11, font=('Consolas', 9), wrap='none')
        code_e.pack(fill='both', expand=True, pady=3)

        def do_publish():
            ver = ver_e.get().strip()
            if not ver:
                messagebox.showwarning('提示', '请填写版本号')
                return
            at = self._norm_dt(at_e.get())
            if at_e.get().strip() and not at:
                messagebox.showwarning('提示', '更新时间格式不对，请按 2026-12-31 18:00 填写')
                return
            code = code_e.get('1.0', 'end')
            if not code.strip():
                messagebox.showwarning('提示', '请粘贴新代码（完整程序文件内容）')
                return
            ok, msg = DB.publish_system_update(ver, note_e.get('1.0', 'end').strip(), at,
                                               code, self.user.get('name') or '')
            messagebox.showinfo('成功' if ok else '提示', msg)
            if ok:
                self._show_update_admin()

        bar0 = tk.Frame(form, bg=COLOR_CONTENT_BG)
        bar0.pack(fill='x', pady=4)
        make_flat_button(bar0, '📤 发布更新', do_publish, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=14, height=1).pack(side='right', padx=6)

        list_f = tk.LabelFrame(self.main_frame, text='更新列表', font=('Arial', 12, 'bold'),
                               bg=COLOR_CONTENT_BG, padx=8, pady=4)
        list_f.pack(fill='x', padx=24, pady=(4, 10))
        frame, tree = make_tree(list_f, ['版本', '更新时间', '更新内容', '发布人', '状态', '代码大小'],
                                {'版本': 80, '更新时间': 150, '更新内容': 330, '发布人': 100,
                                 '状态': 80, '代码大小': 100}, height=6)
        frame.pack(fill='x')
        rows = {}
        for u in DB.list_system_updates():
            rows[str(u['id'])] = u
            tree.insert('', 'end', iid=str(u['id']),
                        values=(u.get('version') or '', u.get('publish_at') or '', 
                                (u.get('note') or '').replace('\n', ' ')[:60],
                                u.get('publisher') or '—', u.get('status') or '',
                                '%d 字符' % len(u.get('code_text') or '')))

        def do_apply_local():
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请先选中一个更新')
                return
            self._apply_update_ui(rows[s[0]])

        def do_del():
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请先选中一个更新')
                return
            if not messagebox.askyesno('确认', '删除更新包 v%s？' % rows[s[0]].get('version')):
                return
            ok, msg = DB.delete_system_update(int(s[0]))
            messagebox.showinfo('提示', msg)
            self._show_update_admin()

        bar = tk.Frame(list_f, bg=COLOR_CONTENT_BG)
        bar.pack(fill='x', pady=4)
        make_flat_button(bar, '✅ 本机立即应用', do_apply_local, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                         width=15, height=1).pack(side='left', padx=5)
        make_flat_button(bar, '🗑️ 删除更新包', do_del, COLOR_DANGER, COLOR_DANGER_HOVER,
                         width=13, height=1).pack(side='left', padx=5)

    def _show_update_center(self):
        self._clear_main()
        make_page_title(self.main_frame, '🔄 系统更新').pack(pady=16)
        cur = DB.current_update()
        if cur:
            head = '当前版本：v%s（%s 应用）' % (cur.get('version') or '', cur.get('applied_at') or '')
        else:
            head = '当前版本：初始版本（还没有应用过更新）'
        tk.Label(self.main_frame, text=head, font=('Arial', 13, 'bold'),
                 bg=COLOR_CONTENT_BG, fg=COLOR_PRIMARY).pack(pady=(0, 4))
        if cur and cur.get('note'):
            tk.Label(self.main_frame, text='本次更新内容：' + (cur.get('note') or ''),
                     font=('Arial', 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY,
                     wraplength=1080, justify='left').pack(pady=(0, 8))
        frame, tree = make_tree(self.main_frame, ['版本', '更新时间', '更新内容', '发布人', '状态'],
                                {'版本': 90, '更新时间': 160, '更新内容': 480, '发布人': 110, '状态': 90},
                                height=13)
        frame.pack(fill='both', expand=True, padx=25)
        rows = {}
        for u in DB.list_system_updates():
            iid = tree.insert('', 'end', values=(u.get('version') or '', u.get('publish_at') or '',
                                                 (u.get('note') or '').replace('\n', ' ')[:70],
                                                 u.get('publisher') or '—', u.get('status') or ''))
            rows[iid] = u

        def do_apply(_e=None):
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请先选中要更新的版本')
                return
            self._apply_update_ui(rows[s[0]])

        def do_view(_e=None):
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请先选中一个版本')
                return
            u = rows[s[0]]
            win = tk.Toplevel(self.root)
            win.title('更新详情 v%s' % (u.get('version') or ''))
            win.geometry('640x460')
            win.configure(bg='white')
            tk.Label(win, text='v%s' % (u.get('version') or ''), font=('Arial', 16, 'bold'),
                     bg='white', fg=COLOR_PRIMARY).pack(pady=(14, 4))
            tk.Label(win, text='更新时间：%s    发布人：%s    状态：%s' % (
                u.get('publish_at') or '—', u.get('publisher') or '—', u.get('status') or ''),
                font=('Arial', 10), bg='white', fg=COLOR_TEXT_GRAY).pack()
            tk.Label(win, text='更新内容', font=('Arial', 11, 'bold'), bg='white').pack(anchor='w', padx=20, pady=(10, 2))
            t2 = tk.Text(win, font=('Arial', 11), wrap='word', bd=1, relief=tk.SOLID)
            t2.insert('1.0', u.get('note') or '（无说明）')
            t2.config(state='disabled')
            t2.pack(fill='both', expand=True, padx=20, pady=(0, 10))
            make_flat_button(win, '关闭', win.destroy, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=10, height=1).pack(pady=10)
            win.transient(self.root)

        tree.bind('<Double-1>', do_view)
        bar = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        bar.pack(fill='x', padx=25, pady=10)
        make_flat_button(bar, '✅ 更新到所选版本', do_apply, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                         width=17, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '👀 查看更新内容', do_view, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=15, height=1).pack(side='left', padx=6)
        tk.Label(bar, text='每台设备同一版本只更新一次', font=('Arial', 9),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(side='left', padx=10)

    # ========== 第 4 批：蛋国建国文件 ==========
    def _render_founding_docs(self, parent):
        """政务公开 · 蛋国建国文件查看：列表 + 双击看详情；现任总统/管理员/袁泓涵可编辑。"""
        top = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        top.pack(fill='x', padx=20, pady=(10, 4))
        tk.Label(top, text='蛋国建国时期的重要文件 · 双击某一行查看全文与照片',
                 font=('Arial', 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(side='left')
        can_edit = DB.is_super_user(self.user)
        frame, tree = make_tree(parent, ['标题', '有照片', '照片介绍', '最后更新', '编辑人'],
                                {'标题': 300, '有照片': 80, '照片介绍': 320, '最后更新': 160, '编辑人': 110},
                                height=12)
        frame.pack(fill='both', expand=True, padx=20, pady=6)
        rows = {}

        def reload():
            tree.delete(*tree.get_children())
            rows.clear()
            for d in DB.list_founding_docs():
                iid = tree.insert('', 'end', values=(d.get('title') or '',
                                                     '🖼 有' if (d.get('image_b64') or '') else '—',
                                                     d.get('image_note') or '—',
                                                     d.get('updated_at') or d.get('created_at') or '',
                                                     d.get('updated_by') or '—'))
                rows[iid] = d

        def sel():
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请先选中一份文件')
                return None
            return rows[s[0]]

        def do_view(_e=None):
            d = sel()
            if d:
                self._founding_doc_detail(d)

        def do_add():
            self._founding_doc_editor(None, reload)

        def do_edit():
            d = sel()
            if d:
                self._founding_doc_editor(d['id'], reload)

        def do_del():
            d = sel()
            if d and messagebox.askyesno('确认', '删除建国文件「%s」？' % (d.get('title') or '')):
                ok, msg = DB.delete_founding_doc(d['id'])
                messagebox.showinfo('提示', msg)
                reload()

        tree.bind('<Double-1>', do_view)
        reload()
        if can_edit:
            bar = tk.Frame(parent, bg=COLOR_CONTENT_BG)
            bar.pack(fill='x', padx=20, pady=(0, 10))
            make_flat_button(bar, '➕ 新增文件', do_add, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                             width=12, height=1).pack(side='left', padx=5)
            make_flat_button(bar, '✏️ 编辑所选', do_edit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=13, height=1).pack(side='left', padx=5)
            make_flat_button(bar, '🗑️ 删除所选', do_del, COLOR_DANGER, COLOR_DANGER_HOVER,
                             width=13, height=1).pack(side='left', padx=5)
            tk.Label(bar, text='（现任总统 / 管理员 / 袁泓涵可编辑，添加照片必须补照片介绍）',
                     font=('Arial', 9), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(side='left', padx=8)
        else:
            tk.Label(parent, text='建国文件由现任总统维护，如需更正请联系总统。',
                     font=('Arial', 9), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 8))

    def _founding_doc_detail(self, doc):
        win = tk.Toplevel(self.root)
        win.title('建国文件')
        win.geometry('760x680')
        win.configure(bg='white')
        tk.Label(win, text=doc.get('title') or '', font=('Arial', 16, 'bold'), bg='white',
                 wraplength=700, justify='left').pack(pady=(14, 4), padx=20)
        tk.Label(win, text='最后更新：%s    编辑人：%s' % (doc.get('updated_at') or '—',
                                                     doc.get('updated_by') or '—'),
                 font=('Arial', 9), bg='white', fg=COLOR_TEXT_GRAY).pack()
        hold = tk.Frame(win, bg='white')
        hold.pack(fill='both', expand=True, padx=20, pady=8)
        img_b64 = doc.get('image_b64') or ''
        if img_b64:
            try:
                raw = base64.b64decode(img_b64)
                im = Image.open(io.BytesIO(raw)).convert('RGB')
                im.thumbnail((700, 330))
                ph = ImageTk.PhotoImage(im)
                lbl = tk.Label(hold, image=ph, bg='white')
                lbl.image = ph
                lbl.pack(pady=(0, 4))
                tk.Label(hold, text='照片介绍：' + (doc.get('image_note') or '—'), font=('Arial', 10),
                         bg='white', fg='#555555', wraplength=700, justify='left').pack(anchor='w', pady=(0, 8))
            except Exception:
                tk.Label(hold, text='（照片读取失败）', font=('Arial', 10), bg='white',
                         fg=COLOR_DANGER).pack()
        txt = tk.Text(hold, font=('Arial', 11), wrap='word', bd=1, relief=tk.SOLID)
        txt.insert('1.0', doc.get('content') or '（无正文）')
        txt.config(state='disabled')
        txt.pack(fill='both', expand=True)
        make_flat_button(win, '关闭', win.destroy, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(pady=10)
        win.transient(self.root)

    def _founding_doc_editor(self, did=None, on_saved=None):
        doc = DB.get_founding_doc(did) if did else None
        win = tk.Toplevel(self.root)
        win.title('编辑建国文件' if doc else '新增建国文件')
        win.geometry('780x660')
        win.configure(bg='white')
        tk.Label(win, text='编辑建国文件' if doc else '新增建国文件', font=('Arial', 14, 'bold'),
                 bg='white').pack(pady=10)
        state = {'b64': (doc.get('image_b64') or '') if doc else ''}
        r1 = tk.Frame(win, bg='white')
        r1.pack(fill='x', padx=20, pady=4)
        tk.Label(r1, text='文件标题：', bg='white', font=('Arial', 11)).pack(side='left')
        title_e = make_groove_entry(r1, width=50)
        title_e.insert(0, (doc or {}).get('title') or '')
        title_e.pack(side='left', padx=4)
        tk.Label(win, text='文件正文：', bg='white', font=('Arial', 11)).pack(anchor='w', padx=20, pady=(6, 0))
        body_e = scrolledtext.ScrolledText(win, height=9, font=('Arial', 11), wrap='word')
        body_e.insert('1.0', (doc or {}).get('content') or '')
        body_e.pack(fill='x', padx=20, pady=4)
        r2 = tk.Frame(win, bg='white')
        r2.pack(fill='x', padx=20, pady=4)
        tk.Label(r2, text='照片介绍（有照片必填）：', bg='white', font=('Arial', 11)).pack(side='left')
        note_e = make_groove_entry(r2, width=40)
        note_e.insert(0, (doc or {}).get('image_note') or '')
        note_e.pack(side='left', padx=4)
        prev = tk.Label(win, text='（未选择照片）', font=('Arial', 10), bg='white', fg=COLOR_TEXT_GRAY)
        prev.pack(pady=4)
        if state['b64']:
            try:
                raw = base64.b64decode(state['b64'])
                im = Image.open(io.BytesIO(raw)).convert('RGB')
                im.thumbnail((420, 180))
                ph = ImageTk.PhotoImage(im)
                prev.config(image=ph, text='')
                prev.image = ph
            except Exception:
                pass

        def pick_image():
            path = filedialog.askopenfilename(title='选择照片',
                                              filetypes=[('图片', '*.jpg *.jpeg *.png *.bmp *.gif')])
            if not path:
                return
            try:
                data = open(path, 'rb').read()
                im = Image.open(io.BytesIO(data)).convert('RGB')
                im.thumbnail((900, 900))
                buf = io.BytesIO()
                im.save(buf, format='JPEG', quality=85)
                state['b64'] = base64.b64encode(buf.getvalue()).decode('ascii')
                disp = im.copy()
                disp.thumbnail((420, 180))
                ph = ImageTk.PhotoImage(disp)
                prev.config(image=ph, text='')
                prev.image = ph
            except Exception as e:
                messagebox.showwarning('提示', '这张图片读不出来：%s' % e)

        def remove_image():
            state['b64'] = ''
            prev.config(image='', text='（已移除照片）')
            prev.image = None

        r3 = tk.Frame(win, bg='white')
        r3.pack(fill='x', padx=20, pady=6)
        make_flat_button(r3, '📷 选择照片', pick_image, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=12, height=1).pack(side='left', padx=4)
        make_flat_button(r3, '🗑️ 移除照片', remove_image, COLOR_DANGER, COLOR_DANGER_HOVER,
                         width=12, height=1).pack(side='left', padx=4)

        def save():
            title = title_e.get().strip()
            if not title:
                messagebox.showwarning('提示', '请填写文件标题')
                return
            if not (body_e.get('1.0', 'end').strip() or state['b64']):
                messagebox.showwarning('提示', '正文和照片至少要有一项')
                return
            if state['b64'] and not note_e.get().strip():
                messagebox.showwarning('提示', '添加了照片就必须填写「照片介绍」')
                return
            ok, msg = DB.save_founding_doc(did if doc else None, title,
                                           body_e.get('1.0', 'end').strip(), state['b64'],
                                           note_e.get().strip(), self.user.get('name') or '')
            messagebox.showinfo('提示', msg if ok else '保存失败')
            if ok:
                win.destroy()
                if on_saved:
                    try:
                        on_saved()
                    except Exception:
                        pass

        make_flat_button(win, '💾 保存', save, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                         width=12, height=1).pack(pady=10)
        win.transient(self.root)


        """首次登录/新任期协议门禁：存在未签署任职协议时，必须同意后方可进行官员操作。"""
        try:
            if self.user.get('type') != 'official':
                return
            ag = DB.pending_agreement(self.user['id'])
            if not ag:
                self._agreement_blocked = False
                return
            self._agreement_blocked = True
            pos = ag['position'] or self.user.get('position') or '—'
            dept = ag['department'] or self.user.get('department') or '—'
            ts = (ag['term_start'] or '')[:10]
            te = (ag['term_end'] or '')[:10]
            term_txt = f'{ts} 至 {te}' if ts and te else '—'
            is_pres = pos in ('总统', '副总统')
            win = tk.Toplevel(self.root)
            win.title('🎉 总统/副总统入职协议' if is_pres else '任职协议 · 需签署')
            win.geometry('640x560' if is_pres else '560x430')
            win.configure(bg='white')
            win.resizable(False, False)
            win.transient(self.root)
            win.grab_set()
            win.attributes('-topmost', True)
            if is_pres:
                tk.Label(win, text='👑 阁下，恭喜你当选！', font=("Arial", 18, "bold"),
                         bg='white', fg=COLOR_PRIMARY).pack(pady=(20, 6))
                tk.Label(win, text=f'{self.user.get("name", "")}　·　{dept}{pos}',
                         font=("Arial", 12), bg='white', fg=COLOR_TITLE).pack(pady=2)
                tk.Label(win, text=f'任期：{term_txt}', font=("Arial", 11), bg='white',
                         fg=COLOR_TITLE).pack(pady=2)
                body_text = (
                    '欢迎就任蛋国最高行政职务！您的核心权限包括：\n\n'
                    '　• 发布与签署行政命令；\n'
                    '　• 任免政府官员、调整部门与职务；\n'
                    '　• 主持内阁、领导各部委工作；\n'
                    '　• 批准法案、行使赦免与保释权；\n'
                    '　• 统帅三军、代表蛋国处理外交；\n'
                    '　• 召集并主持大选与权力交接。\n\n'
                    '请及时签署入职协议并开始任职！上帝保佑你！'
                )
            else:
                tk.Label(win, text='📜 蛋国官员任职协议', font=("Arial", 17, "bold"),
                         bg='white', fg=COLOR_PRIMARY).pack(pady=(20, 6))
                tk.Label(win, text=f'签署人：{self.user.get("name", "")}　·　职务：{dept}{pos}',
                         font=("Arial", 11), bg='white', fg=COLOR_TITLE).pack(pady=2)
                tk.Label(win, text=f'本次任期：{term_txt}', font=("Arial", 11), bg='white',
                         fg=COLOR_TITLE).pack(pady=2)
                body_text = (
                    '本人受命担任蛋国政府官员，自愿签署本任职协议并承诺：\n\n'
                    '一、在任期内认真履职，恪尽职守，服从上级依法管理；\n'
                    '二、任期内依法行使职务权限，不滥用职权、不徇私舞弊；\n'
                    '三、任期届满后自动卸任，配合完成职务交接；\n'
                    '四、服从任职协议相关法律与纪律规定。\n\n'
                    '签署本协议后方可进行官员操作；未签署将无法使用官员功能。'
                )
            body = tk.Label(win, text=body_text, font=("Arial", 11), bg='#f4f6fa', fg='#333',
                            justify='left', wraplength=560, padx=16, pady=12)
            body.pack(fill='x', padx=28, pady=12)

            def agree():
                ok, msg = DB.sign_agreement(ag['id'], self.user['id'])
                win.destroy()
                if ok:
                    self._agreement_blocked = False
                    DB.log('official', self.user['id'], self.user['name'], '签署任职协议',
                           f'{"总统" if pos=="总统" else "副总统"}' if is_pres else '官员'
                           + f'同意入职协议（{dept}{pos}，{term_txt}）')
                    self._build_menu()

            def reject():
                # 不退出登录，保持 blocked；下次点菜单按钮会再弹
                win.destroy()

            btns = tk.Frame(win, bg='white')
            btns.pack(pady=16)
            make_flat_button(btns, '✅ 同意并签署入职协议' if is_pres else '✅ 同意并签署协议',
                             agree, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                             width=20, height=1).pack(side='left', padx=8)
            make_flat_button(btns, '✖ 稍后再签', reject, COLOR_GRAY,
                             COLOR_GRAY, width=12, height=1).pack(side='left', padx=8)
            # 关窗 X 也按"稍后再签"处理，不退出登录
            win.protocol('WM_DELETE_WINDOW', reject)
        except Exception:
            pass

    def _build_layout(self):
        # 顶部状态栏（银行系统风格）
        status_bar = tk.Frame(self.root, height=30, bg=COLOR_TOP_BAR)
        status_bar.pack(side=tk.TOP, fill=tk.X)
        role_text = "管理员" if self.user['type'] == 'admin' else ("政府官员" if self.user['type'] == 'official' else ("入籍临时账号" if self.user['type'] == 'temp' else "普通民众"))
        if self.user['type'] == 'temp':
            info = f"当前用户：{self.user['app']['applicant_name']}（{role_text}）"
        else:
            info = f"当前用户：{self.user['name']}（{role_text}）"
        if self.user['type'] == 'official':
            info += f" | {self.user.get('department', '')} · {self.user.get('position', '')}"
        self.status_info_label = tk.Label(status_bar, text=info, bg=COLOR_TOP_BAR, fg='white', font=("Arial", 10))
        self.status_info_label.pack(side=tk.LEFT, padx=20)
        logout_btn = tk.Button(status_bar, text="退出登录", command=self._logout,
                               bg=COLOR_DANGER, fg='white', font=("Arial", 10), relief=tk.FLAT, cursor='hand2')
        logout_btn.pack(side=tk.RIGHT, padx=20, pady=3)
        logout_btn.bind("<Enter>", lambda e: logout_btn.config(bg=COLOR_DANGER_HOVER))
        logout_btn.bind("<Leave>", lambda e: logout_btn.config(bg=COLOR_DANGER))

        # 左侧菜单栏（可滚动）
        menu_container = tk.Frame(self.root, width=200, bg=COLOR_MENU_BG)
        menu_container.pack(side=tk.LEFT, fill=tk.Y)
        menu_container.pack_propagate(False)
        scrollbar = tk.Scrollbar(menu_container)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas = tk.Canvas(menu_container, yscrollcommand=scrollbar.set, width=180, bg=COLOR_MENU_BG, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=canvas.yview)
        self.menu_frame = tk.Frame(canvas, bg=COLOR_MENU_BG)
        canvas.create_window((0, 0), window=self.menu_frame, anchor="nw")

        self._build_menu()

        def configure_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        self.menu_frame.bind("<Configure>", configure_scroll_region)

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", on_mousewheel)

        # 右侧主内容区域
        self.main_frame = tk.Frame(self.root, bg=COLOR_CONTENT_BG)
        self.main_frame.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH)

        self._show_dashboard()
        # 启动时补齐已结束大选的官员档案（幂等，无则补）
        try:
            DB.backfill_election_careers()
        except Exception:
            pass
        # 宪法换届自动大选：自2026年起每隔4年、10月23日系统自动开启一轮大选（启动后延迟检查+每小时轮询）
        try:
            self.root.after(3000, self._schedule_auto_election)
        except Exception:
            pass

    def _schedule_auto_election(self):
        try:
            ok, msg, _eid = DB.auto_regular_election()
            if ok:
                messagebox.showinfo('🗳️ 自动大选', msg)
        except Exception:
            pass
        try:
            ok2, msg2, _e2 = DB.auto_close_election()
            if ok2:
                messagebox.showinfo('🗳️ 大选计票', msg2)
        except Exception:
            pass
        try:
            ok3, msg3, _e3 = DB.auto_inauguration()
            if ok3:
                messagebox.showinfo('🏛️ 总统就职', msg3)
        except Exception:
            pass
        # 任期自动化：到期自动卸任 / 结束前10天自动提交连任 / 卸任前3天自动通过
        try:
            DB.career_tick()
        except Exception:
            pass
        # 法律投票到点自动计票
        try:
            DB.law_tick()
        except Exception:
            pass
        # 国库每月 1 号自动收税+发薪（幂等）
        try:
            ok_m, msg_m = DB.auto_monthly_treasury()
            if ok_m:
                messagebox.showinfo('💰 国库月结', msg_m)
        except Exception:
            pass
        try:
            self.root.after(300_000, self._schedule_auto_election)
        except Exception:
            pass

    def _build_menu(self):
        for w in self.menu_frame.winfo_children():
            w.destroy()
        if self.user['type'] == 'admin':
            menus = [
                ("🏠 系统概览", self._show_dashboard),
                ("👤 官员任免", self._show_official_mgmt),
                ("🏛️ 部门管理", self._show_department_mgmt),
                ("🎖️ 职级管理", self._show_rank_mgmt),
                ("👔 职务管理", self._show_position_mgmt),
                ("🔑 权限管理", self._show_permission_mgmt),
                ("👥 官员名录", self._show_official_directory),
                ("🏛️ 政治结构", self._show_political_structure),
                ("🏛️ 议会中心", self._show_parliament),
                ("🚨 弹劾管理", self._show_impeachment),
                ("📜 修宪审议", self._show_constitution),
                ("🎭 党派管理", self._show_party_mgmt),
                ("🗳️ 大选管理", self._show_election_center),
                ("📚 事项管理", self._show_items_admin),
                ("🗂️ 办件管理", self._show_cases_admin),
                ("📅 预约管理", self._show_appointments_admin),
                ("📄 公文管理", self._show_docs_admin),
                ("📜 政策管理", self._show_policy_admin),
                ("📢 公告管理", self._show_announcements),
                ("📢 政务公开", self._show_announcements_view),
                ("🚨 举报处理", self._show_reports),
                ("⚖️ 起诉官员", self._show_lawsuit),
                ("💬 咨询管理", self._show_consult_admin),
                ("📨 消息管理", self._show_message_admin),
                ("📝 系统日志", self._show_logs),
                ("📈 数据统计", self._show_stats),
                ("🔐 修改密码", self._show_change_pwd),
            ]
        elif self.user['type'] == 'official':
            p = DB.official_perm(self.user)
            eff = p['eff']
            menus = [("🏠 工作台", self._show_dashboard)]
            if eff['send_clause']:
                menus.append(("📝 发送条款", self._show_send_clause))
            if eff['approve']:
                scope_txt = SCOPE_LABELS.get(p['scope'], p['scope'])
                menus.append((f"📋 待办审批·{scope_txt}", self._show_approval_official))
            if DB.has_president_power(self.user) and DB.can(self.user, 'exec_order'):
                menus.append(("📜 签署/发布行政令", self._show_exec_order))
                menus.append(("📜 行政令管理", self._show_exec_order_mgmt))
                menus.append(("⛓️ 保释罪犯", self._show_bail_prisoners))
            if DB.has_president_power(self.user):
                menus.append(("📜 法律发布", self._show_law_publish))
            if (self.user.get('position') or '') in ('总统', '副总统') or (self.user.get('department') or '') == '国籍管理部门':
                menus.append(("🛂 国籍申请审核", self._show_nationality_review))
            if (self.user.get('position') or '') in ('总统', '副总统') or '部长' in (self.user.get('position') or ''):
                menus.append(("📋 公职申请审核", self._show_job_review))
            menus.insert(0, ("🔔 我的待办", self._show_todo))
            menus.append(("📜 国籍证明", self._show_nationality_cert))
            if (self.user.get('position') or '') in ('总统', '副总统') and DB.can(self.user, 'appoint_official'):
                menus.append(("👤 官员任免", self._show_official_mgmt))
            if DB.has_president_power(self.user):
                menus.append(("🏛️ 部门管理", self._show_department_mgmt))
            menus.append(("📋 离职申请", self._show_resign_apply))
            _rpos = self.user.get('position') or ''
            if _rpos in ('总统', '副总统', '国务卿') or '部长' in _rpos or '局长' in _rpos or '署长' in _rpos:
                menus.append(("📋 离职审批", self._show_resign_review))
            # ===== 任期管理 / 连任审批 / 合同管理（新需求） =====
            if _rpos in ('总统', '副总统') or '部长' in _rpos or '局长' in _rpos or '署长' in _rpos or '处长' in _rpos:
                _tm_label = '🗓️ 官员任期管理' if _rpos in ('总统', '副总统') else '🗓️ 本部门任期管理'
                menus.append((_tm_label, self._show_tenure_mgmt))
                menus.append(("📋 连任审批", self._show_renewal_review))
            if _rpos in ('总统', '副总统') or '部长' in _rpos or '局长' in _rpos or '署长' in _rpos or '处长' in _rpos:
                menus.append(("📄 合同管理", self._show_contract_mgmt))
            menus.append(("📄 我的合同", self._show_my_contracts))
            menus.append(("🗳️ 会议投票", self._show_meeting_center))
            if _rpos in ('参议员', '众议员', '参议院议长', '众议院议长'):
                menus.append(("📜 法律投票", self._show_law_vote))
            if eff['handle']:
                menus.append(("🗂️ 办件办理", self._show_cases_official))
            # 公文管理：收文人人可看，“发文”按钮在页面内按权限控制
            menus.append(("📄 公文管理", self._show_docs_official))
            menus.append(("📜 审批历史", self._show_approval_history))
            if eff['handle']:
                menus.append(("💬 互动办理", self._show_interact_official))
                menus.append(("📄 工作报告", self._show_work_report))
            menus += [
                ("👥 官员名录", self._show_official_directory),
                ("🏛️ 政治结构", self._show_political_structure),
                ("🏛️ 党派中心", self._show_party_portal),
                ("🏛️ 议会中心", self._show_parliament),
                ("📜 修宪审议", self._show_constitution),
            ]
            if DB.can(self.user, 'impeach'):
                menus.append(("🚨 弹劾管理", self._show_impeachment))
            if (self.user.get('position') or '') in ('参议院议长', '众议院议长'):
                menus.append(("📥 参选审核", self._show_candidacy_review))
            menus += [
                ("🗳️ 申请参选总统", self._show_candidacy_apply),
                ("🗳️ 大选中心", self._show_election_center),
                ("💰 国库财政", self._show_treasury),
                ("⚖️ 违宪审查", self._show_constitutional_cases),
                ("📰 新闻联播", self._show_news),
                ("🗳️ 公投中心", self._show_referenda_admin),
                ("📢 政务公开", self._show_announcements_view),
                ("👤 我的信息", self._show_my_info),
                ("📨 我的消息", self._show_my_messages),
            ]
        elif self.user['type'] == 'temp':
            menus = [
                ("📋 我的入籍申请进度", self._show_temp_status),
            ]
        else:
            menus = [
                ("🏠 办事首页", self._show_dashboard),
                ("🏛️ 办事服务大厅", self._show_service_hall),
                ("📋 我的办件", self._show_my_cases),
                ("📅 办事预约", self._show_my_appointments),
                ("🔍 办件进度查询", self._show_case_query),
                ("🗳️ 总统大选", self._show_election_center),
                ("🗳️ 申请参选总统", self._show_candidacy_apply),
                ("📨 入党申请", self._show_party_apply),
                ("📢 政务公开", self._show_announcements_view),
                ("💬 互动交流", self._show_interact_citizen),
                ("📝 我的申请", self._show_my_applications),
                ("🪪 我的户籍", self._show_my_household),
                ("📜 国籍证明", self._show_nationality_cert),
                ("🏠 居住地管理", self._show_state_select),
                ("✍️ 请愿签名", self._show_petitions_citizen),
                ("🗳️ 公投投票", self._show_referenda_citizen),
                ("🏛️ 公职申请", self._show_job_apply),
                ("📋 我的公职申请", self._show_my_job_apps),
                ("📨 我的消息", self._show_my_messages),
                ("👥 官员名录", self._show_official_directory),
                ("🏛️ 政治结构", self._show_political_structure)
            ]
        # ---- 第 4 批：三端通用的「特别活动」「系统更新」入口；授权人再加发布/管理入口 ----
        try:
            if self.user.get('type') != 'temp':
                menus.append(("🎉 特别活动", self._show_special_events_view))
                menus.append(("🔄 系统更新", self._show_update_center))
                menus.append(("🐞 反馈漏洞", self._show_bug_report))
                menus.append(("🎁 抽奖活动", self._show_lottery_view))
                menus.append(("🗳️ 单人候选人名单", self._show_solo_candidates))
                if DB.is_super_user(self.user):
                    _txts = [t for t, _c in menus]
                    if '🎉 特别活动管理' not in _txts:
                        menus.append(("🎉 特别活动管理", self._show_special_event_admin))
                    if '🔄 系统更新管理' not in _txts:
                        menus.append(("🔄 系统更新管理", self._show_update_admin))
                    if '📢 公告管理' not in _txts and '📢 发布公告' not in _txts:
                        menus.append(("📢 发布公告", self._show_announcements))
                    if '🐞 漏洞处理' not in _txts:
                        menus.append(("🐞 漏洞处理", self._show_bug_admin))
                    if '🎁 抽奖管理' not in _txts:
                        menus.append(("🎁 抽奖管理", self._show_lottery_admin))
                    if '📨 消息发送' not in _txts and '📨 消息管理' not in _txts:
                        menus.append(("📨 消息发送", self._show_message_admin))
                    if '📝 单人候选审核' not in _txts:
                        menus.append(("📝 单人候选审核", self._show_solo_admin))
                    if (self.user.get('position') or '') == '总统' and '🕊️ 权力暂时交接' not in _txts:
                        menus.append(("🕊️ 权力暂时交接", self._show_power_transfer))
        except Exception:
            pass

        for text, command in menus:
            def _wrap(orig=command):
                def _on_click():
                    if self.user.get('type') == 'official' and getattr(self, '_agreement_blocked', False):
                        messagebox.showwarning('请同意协议',
                                               '您尚未签署任职协议，无法行使职权。\n'
                                               '请先在弹窗中签署协议。')
                        self._check_agreement()
                        return
                    orig()
                return _on_click
            btn = tk.Button(self.menu_frame, text=text, command=_wrap(),
                            width=18, height=2, bg=COLOR_MENU_BTN, relief=tk.FLAT, font=("Arial", 10), cursor='hand2')
            btn.pack(pady=6, padx=8)
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg=COLOR_MENU_HOVER))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=COLOR_MENU_BTN))

    def _clear_main(self):
        for widget in self.main_frame.winfo_children():
            widget.destroy()

    # ========== 国籍申请相关 GUI ==========
    def _show_temp_status(self):
        """临时账号登录后看到的入籍申请进度页。"""
        self._clear_main()
        app = self.user['app']
        tk.Label(self.main_frame, text="📋 我的入籍申请进度", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=20)
        box = tk.Frame(self.main_frame, bg='#f8f9fa', bd=1, relief=tk.SOLID)
        box.pack(padx=40, pady=10, fill='x')
        rows = [
            ('姓名', app['applicant_name']),
            ('原国籍', app['original_nationality']),
            ('出生日期', app['birth_date']),
            ('性别', app['gender']),
            ('外国护照号', app['foreign_passport_no'] or '—'),
            ('申请原因', app['reason']),
            ('提交时间', app['created_at']),
            ('当前状态', app['status']),
        ]
        if app['reviewer_name']:
            rows.append(('审核人', f"{app['reviewer_name']}（{app['reviewed_at']}）"))
        if app['reviewer_note']:
            rows.append(('审核备注', app['reviewer_note']))
        if app['assigned_id_number']:
            rows.append(('分配身份证号', app['assigned_id_number']))
        for i, (k, v) in enumerate(rows):
            tk.Label(box, text=k, bg='#f8f9fa', font=("Arial", 10, "bold")).grid(row=i, column=0, sticky='w', padx=15, pady=6)
            tk.Label(box, text=str(v), bg='#f8f9fa', font=("Arial", 10), wraplength=500, justify='left').grid(row=i, column=1, sticky='w', padx=15, pady=6)

        if app['status'] == '已通过':
            tk.Label(self.main_frame,
                     text=f"🎉 恭喜！您已获准加入蛋国国籍！\n您的身份证号：{app['assigned_id_number']}\n姓名：{app['applicant_name']}\n请点击下方按钮确认入籍，之后此临时账号将注销，请改用身份证号+姓名登录。",
                     bg='#e8f5e9', fg='#2e7d32', font=("Arial", 12, "bold"), justify='center').pack(pady=15, padx=40, fill='x')
            def confirm():
                if not messagebox.askyesno("确认入籍", "确认您已获得蛋国国籍？\n确认后此临时账号将立即注销。"):
                    return
                DB.confirm_nationality(app['id'])
                messagebox.showinfo("完成", f"入籍确认完成！\n请退出登录，使用：\n身份证号：{app['assigned_id_number']}\n姓名：{app['applicant_name']}\n以「普通民众」身份登录。")
                self._logout()
            make_flat_button(self.main_frame, "✅ 确认入籍并注销临时账号", confirm,
                             COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(pady=10)
        elif app['status'] == '待审核':
            def withdraw():
                if not messagebox.askyesno("撤销申请", "确定撤销本次入籍申请？"):
                    return
                DB.withdraw_nationality(app['id'])
                messagebox.showinfo("已撤销", "申请已撤销，临时账号已注销。")
                self._logout()
            make_flat_button(self.main_frame, "❌ 撤销申请", withdraw, COLOR_DANGER, COLOR_DANGER_HOVER).pack(pady=10)
        elif app['status'] == '已拒绝':
            tk.Label(self.main_frame, text="很遗憾，您的入籍申请未获批准。",
                     bg='#ffebee', fg=COLOR_DANGER, font=("Arial", 12)).pack(pady=15)
        elif app['status'] == '待补充材料':
            tk.Label(self.main_frame,
                     text="📝 审核人要求您补充以下材料：必须按要求的文件类型上传（要求图片就传 jpg/png，要求文档就传 docx）",
                     bg='#fff8e1', fg='#e65100', font=("Arial", 12, "bold"),
                     wraplength=700, justify='left').pack(pady=10, padx=40, fill='x')
            reqs = DB.parse_requirements(app.get('requested_materials') or '')
            uploaded = {}
            try:
                for a in DB.list_attachments(app['id']):
                    uploaded[a.get('req_item') or ''] = a
            except Exception:
                uploaded = {}
            lst = tk.Frame(self.main_frame, bg='#fff8e1')
            lst.pack(fill='x', padx=40, pady=4)
            if not reqs:
                tk.Label(lst, text=app.get('requested_materials') or '（审核人未列明具体要求）',
                         bg='#fff8e1', fg='#5d4037', font=("Arial", 10),
                         justify='left', wraplength=600).pack(anchor='w')
            for r in reqs:
                row = tk.Frame(lst, bg='#fff8e1')
                row.pack(fill='x', pady=4)
                done = uploaded.get(r['item'])
                tk.Label(row, text='• %s（要求：%s）' % (r['item'], DB.expected_type_label(r['type'])),
                         bg='#fff8e1', fg='#3e2723', font=("Arial", 10)).pack(side='left')
                tk.Label(row, text=('  ✅ 已上传：%s' % done.get('file_name')) if done else '  ⚠ 未上传',
                         bg='#fff8e1', fg=(COLOR_SUCCESS if done else COLOR_DANGER),
                         font=("Arial", 9)).pack(side='left')

                def _make_uploader(item=r['item'], typ=r['type']):
                    def _up():
                        p = filedialog.askopenfilename(parent=self.root, title='上传「%s」' % item)
                        if not p:
                            return
                        ok, msg = DB.upload_attachment(app['id'], item, typ, p)
                        if ok:
                            messagebox.showinfo('已上传', msg)
                            self._show_temp_status()
                        else:
                            messagebox.showerror('上传失败', msg)
                    return _up

                make_flat_button(row, '📎 上传', _make_uploader(),
                                 COLOR_PRIMARY, COLOR_PRIMARY_HOVER).pack(side='left', padx=8)
                if done and (done.get('file_ext') or '').lower() in DB.IMG_EXT:
                    def _make_viewer(aid=done.get('id')):
                        def _v():
                            blob = DB.attachment_blob(aid)
                            if not blob or not blob.get('data_b64'):
                                messagebox.showwarning('打不开', '附件内容读取失败')
                                return
                            try:
                                raw = base64.b64decode(blob['data_b64'])
                                img = Image.open(io.BytesIO(raw)).convert('RGB')
                                img.thumbnail((520, 520))
                                top = tk.Toplevel(self.root)
                                top.title(blob.get('file_name') or '预览')
                                ph = ImageTk.PhotoImage(img)
                                lbl = tk.Label(top, image=ph)
                                lbl.image = ph
                                lbl.pack()
                            except Exception as e:
                                messagebox.showwarning('打不开', str(e))
                        return _v
                    make_flat_button(row, '👁 预览', _make_viewer(),
                                     '#5c6bc0', '#3949ab').pack(side='left', padx=2)
            if app.get('submitted_materials'):
                tk.Label(self.main_frame, text=f"您上次提交的补充说明：\n{app['submitted_materials']}",
                         bg='#e8f5e9', fg='#2e7d32', font=("Arial", 9),
                         justify='left', wraplength=600).pack(pady=5, padx=40, fill='x')
            tk.Label(self.main_frame, text="补充说明（可选，可写情况说明）：",
                     bg='white', font=("Arial", 10)).pack(pady=(8, 2))
            txt = tk.Text(self.main_frame, width=70, height=4, font=("Arial", 10))
            txt.pack(pady=2, padx=40)

            def submit_supp():
                content = txt.get('1.0', 'end').strip()
                ok, msg = DB.submit_materials(app['id'], content)
                if ok:
                    messagebox.showinfo("已提交", msg)
                    self._show_temp_status()
                else:
                    messagebox.showerror("还差材料", msg)
            make_flat_button(self.main_frame, "📨 提交补充材料", submit_supp,
                             COLOR_PRIMARY, COLOR_PRIMARY_HOVER).pack(pady=10)

    def _show_todo(self):
        """官员待办聚合页：汇总国籍/公职/审批/办件等待处理事项。"""
        self._clear_main()
        tk.Label(self.main_frame, text="🔔 我的待办", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=15)
        pos = self.user.get('position', '')
        dept = self.user.get('department', '')
        is_pres = pos in ('总统', '副总统')
        is_minister = '部长' in pos
        rows = []
        conn = DB.get_conn(GOV_DB)
        try:
            # 国籍申请
            if is_pres or dept == '国籍管理部门':
                n = conn.execute("SELECT COUNT(*) FROM nationality_applications WHERE status='待审核'").fetchone()[0]
                if n: rows.append(('🛂', '国籍申请待审核', n, '件入籍申请待您处理', self._show_nationality_review))
            # 公职申请
            if is_pres:
                n = conn.execute("SELECT COUNT(*) FROM public_job_applications WHERE status='待审核'").fetchone()[0]
                if n: rows.append(('📋', '公职申请待审核（全部部门）', n, '件公职申请待您批准', self._show_job_review))
            elif is_minister:
                n = conn.execute("SELECT COUNT(*) FROM public_job_applications WHERE status='待审核' AND department=?", (dept,)).fetchone()[0]
                if n: rows.append(('📋', f'{dept} 公职申请待审核', n, '件本部门公职申请待审', self._show_job_review))
            # 待审批文件
            if is_minister or is_pres:
                if is_pres:
                    n = conn.execute("SELECT COUNT(*) FROM approval_files WHERE status='待审批'").fetchone()[0]
                else:
                    n = conn.execute("SELECT COUNT(*) FROM approval_files WHERE status='待审批' AND (target_department=? OR target_department IS NULL OR target_department='')", (dept,)).fetchone()[0]
                if n: rows.append(('📑', '待审批公文', n, '份公文待您审批', None))
            # 本部门办件
            if dept:
                n = conn.execute("SELECT COUNT(*) FROM service_cases WHERE department=? AND status IN ('待受理','受理中','办理中')", (dept,)).fetchone()[0]
                if n: rows.append(('🗂️', '本部门在办件', n, '件办件在流程中', None))
            # 弹劾/法律投票（议员）
            if pos in ('参议员', '众议员', '参议院议长', '众议院议长'):
                n = conn.execute("SELECT COUNT(*) FROM laws WHERE status='议会投票中'").fetchone()[0]
                if n: rows.append(('⚖️', '待投票法律', n, '部法律待您投票', None))
        finally:
            conn.close()
        if not rows:
            tk.Label(self.main_frame, text="✅ 暂无待办事项，全部已处理完毕。",
                     bg='white', fg=COLOR_SUCCESS, font=("Arial", 13, "bold")).pack(pady=50)
            return
        for icon, title, cnt, desc, goto in rows:
            card = tk.Frame(self.main_frame, bg='#f8f9fa', bd=1, relief=tk.SOLID)
            card.pack(fill='x', padx=40, pady=6)
            tk.Label(card, text=icon, bg='#f8f9fa', font=("Arial", 20)).pack(side='left', padx=15, pady=10)
            mid = tk.Frame(card, bg='#f8f9fa')
            mid.pack(side='left', fill='x', expand=True)
            tk.Label(mid, text=title, bg='#f8f9fa', font=("Arial", 12, "bold")).pack(anchor='w')
            tk.Label(mid, text=desc, bg='#f8f9fa', fg=COLOR_GRAY).pack(anchor='w')
            tk.Label(card, text=str(cnt), bg='#f8f9fa', fg=COLOR_DANGER,
                     font=("Arial", 22, "bold")).pack(side='right', padx=20)
            if goto:
                card.bind('<Button-1>', lambda e, g=goto: g())
                for w in card.winfo_children():
                    w.bind('<Button-1>', lambda e, g=goto: g())

    def _show_nationality_review(self):
        """总统/副总统审核入籍申请。双击任意一行查看完整申请信息、人脸与补充材料。"""
        self._clear_main()
        tk.Label(self.main_frame, text="🛂 国籍申请审核", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=15)
        tk.Label(self.main_frame,
                 text="💡 双击表格中的任意一行，可以查看该人的完整申请信息、人脸照片与已上传的补充材料",
                 bg='white', fg='#1565c0', font=("Arial", 9)).pack()
        apps = DB.list_nationality_pending()
        if not apps:
            tk.Label(self.main_frame, text="暂无待审核的入籍申请。", bg='white', fg=COLOR_GRAY,
                     font=("Arial", 12)).pack(pady=40)
            return
        cols = ('id', 'status', 'name', 'orig', 'birth', 'passport', 'materials', 'created')
        tree = ttk.Treeview(self.main_frame, columns=cols, show='headings', height=10)
        for c, t, w in [('id', 'ID', 45), ('status', '状态', 95), ('name', '姓名', 85),
                        ('orig', '原国籍', 90), ('birth', '出生日期', 95),
                        ('passport', '外国护照号', 110), ('materials', '补充材料', 130),
                        ('created', '提交时间', 135)]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor='center')
        tree.pack(fill='both', expand=True, padx=15, pady=5)
        for a in apps:
            try:
                atts = DB.list_attachments(a['id'])
            except Exception:
                atts = []
            reqs = DB.parse_requirements(a.get('requested_materials') or '')
            if reqs:
                mat_txt = '%d/%d 已传' % (len(atts), len(reqs))
            else:
                mat_txt = '%d 个附件' % len(atts) if atts else '—'
            tree.insert('', 'end', values=(a['id'], a['status'], a['applicant_name'],
                                          a['original_nationality'], a['birth_date'],
                                          a.get('foreign_passport_no') or '', mat_txt,
                                          a['created_at']))

        def selected_aid():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("提示", "请先选中一条申请")
                return None
            try:
                return int(tree.item(sel[0], 'values')[0])
            except Exception:
                return None

        def open_detail(event=None):
            aid = selected_aid()
            if aid:
                self._show_nationality_detail(aid)

        tree.bind('<Double-1>', open_detail)

        def do_review(approve):
            aid = selected_aid()
            if not aid:
                return
            note = ''
            if not approve:
                note = simpledialog.askstring("拒绝原因", "请输入拒绝原因（可空）：", parent=self.root) or ''
            ok, msg, id_num = DB.review_nationality(aid, approve, self.user['id'], self.user['name'], note)
            if approve and ok:
                messagebox.showinfo("通过", f"{msg}\n分配身份证号：{id_num}")
            else:
                messagebox.showinfo("结果", msg)
            self._show_nationality_review()

        def request_materials():
            aid = selected_aid()
            if not aid:
                return
            dlg = tk.Toplevel(self.root)
            dlg.title("要求补充材料")
            dlg.geometry("480x400")
            dlg.configure(bg='white')
            dlg.transient(self.root)
            tk.Label(dlg, text="请列明需要申请人补充的材料（每行一项）：",
                     bg='white', font=("Arial", 11, "bold")).pack(pady=8)
            tk.Label(dlg, text="格式：材料名称|类型（竖线分隔，类型决定必须上传什么文件）\n"
                               "类型可填：图片 / 文档 / PDF / 任意（不填默认任意）\n例：\n"
                               "  无犯罪记录证明|文档      ← 申请人必须上传 docx\n"
                               "  本国户籍公证|图片        ← 申请人必须上传 jpg/png",
                     bg='white', fg=COLOR_GRAY, justify='left', font=("Arial", 9)).pack(padx=12, anchor='w')
            txt = tk.Text(dlg, width=52, height=9, font=("Arial", 10))
            txt.pack(pady=8, padx=15)

            def send():
                items = txt.get('1.0', 'end').strip()
                if not items:
                    messagebox.showwarning("提示", "请填写需要补充的材料")
                    return
                parsed = DB.parse_requirements(items)
                if not parsed:
                    messagebox.showwarning("提示", "没有解析出有效材料项")
                    return
                ok, msg = DB.request_materials(aid, items, self.user['name'])
                dlg.destroy()
                preview = '\n'.join('• %s（%s）' % (p['item'], DB.expected_type_label(p['type']))
                                    for p in parsed)
                messagebox.showinfo("已发送", msg + '\n\n本次要求上传：\n' + preview)
                self._show_nationality_review()
            make_flat_button(dlg, "📨 发送补充要求", send, COLOR_WARNING, '#d35400').pack(pady=6)

        bar = tk.Frame(self.main_frame, bg='white')
        bar.pack(fill='x', padx=15, pady=10)
        make_flat_button(bar, "🔍 查看详情（双击也可）", open_detail,
                         '#1565c0', '#0d47a1').pack(side='left', padx=5)
        make_flat_button(bar, "✅ 批准入籍", lambda: do_review(True), COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(side='left', padx=5)
        make_flat_button(bar, "❌ 拒绝", lambda: do_review(False), COLOR_DANGER, COLOR_DANGER_HOVER).pack(side='left', padx=5)
        make_flat_button(bar, "📝 要求补充材料", request_materials, COLOR_WARNING, '#d35400').pack(side='left', padx=5)

    def _show_nationality_detail(self, app_id):
        """审核端：查看某份入籍申请的完整信息 + 人脸照片 + 补充材料（图片内嵌预览 / docx 可导出）。"""
        conn = DB.get_conn(GOV_DB)
        try:
            row = conn.execute("SELECT * FROM nationality_applications WHERE id=?", (app_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            messagebox.showwarning("提示", "该申请不存在")
            return
        app = dict(row)
        try:
            atts = DB.list_attachments(app_id)
        except Exception:
            atts = []
        reqs = DB.parse_requirements(app.get('requested_materials') or '')

        win = tk.Toplevel(self.root)
        win.title('入籍申请详情 #%d - %s' % (app_id, app.get('applicant_name') or ''))
        win.geometry('860x720')
        win.configure(bg='white')
        win.transient(self.root)
        body = tk.Frame(win, bg='white')
        body.pack(fill='both', expand=True, padx=14, pady=10)

        left = tk.Frame(body, bg='white')
        left.pack(side='left', fill='both', expand=True)
        right = tk.Frame(body, bg='white', width=300)
        right.pack(side='right', fill='y', padx=(12, 0))

        tk.Label(left, text='📋 完整申请信息', bg='white', fg=COLOR_TITLE,
                 font=("Arial", 13, "bold")).pack(anchor='w', pady=(0, 6))
        fields = [('申请编号', app.get('id')), ('当前状态', app.get('status')),
                  ('姓名', app.get('applicant_name')), ('原国籍', app.get('original_nationality')),
                  ('出生日期', app.get('birth_date')), ('性别', app.get('gender')),
                  ('外国护照号', app.get('foreign_passport_no') or '（未填）'),
                  ('临时账号', app.get('temp_account')),
                  ('分配身份证号', app.get('assigned_id_number') or '（未分配）'),
                  ('分配地址', app.get('assigned_address') or '—'),
                  ('提交时间', app.get('created_at') or ''),
                  ('审核人', app.get('reviewer_name') or '—'),
                  ('审核时间', app.get('reviewed_at') or '—'),
                  ('审核意见', app.get('reviewer_note') or '—')]
        info = tk.Frame(left, bg='#f7f9fc')
        info.pack(fill='x')
        for i, (k, v) in enumerate(fields):
            tk.Label(info, text=k + '：', bg='#f7f9fc', fg='#546e7a',
                     font=("Arial", 10)).grid(row=i, column=0, sticky='e', padx=(8, 2), pady=2)
            tk.Label(info, text=str(v), bg='#f7f9fc', fg='#263238', font=("Arial", 10),
                     wraplength=420, justify='left').grid(row=i, column=1, sticky='w', padx=(2, 8), pady=2)

        tk.Label(left, text='📝 申请原因', bg='white', fg=COLOR_TITLE,
                 font=("Arial", 12, "bold")).pack(anchor='w', pady=(10, 4))
        reason_box = tk.Text(left, height=5, font=("Arial", 10), wrap='word', bg='#fffde7')
        reason_box.insert('1.0', app.get('reason') or '')
        reason_box.config(state='disabled')
        reason_box.pack(fill='x')

        tk.Label(left, text='📎 补充材料（审核人要求 → 实际上传）', bg='white', fg=COLOR_TITLE,
                 font=("Arial", 12, "bold")).pack(anchor='w', pady=(12, 4))
        mat = tk.Frame(left, bg='white')
        mat.pack(fill='both', expand=True)
        if not reqs and not atts:
            tk.Label(mat, text='（本次未要求补充材料）', bg='white', fg=COLOR_GRAY,
                     font=("Arial", 10)).pack(anchor='w')
        amap = {a.get('req_item') or '': a for a in atts}
        for r in reqs:
            a = amap.get(r['item'])
            rowf = tk.Frame(mat, bg='#f1f8e9' if a else '#ffebee')
            rowf.pack(fill='x', pady=2)
            tk.Label(rowf, text='要求：%s（%s）' % (r['item'], DB.expected_type_label(r['type'])),
                     bg=rowf['bg'], font=("Arial", 10, 'bold'), fg='#2e7d32' if a else '#c62828').pack(side='left', padx=6, pady=4)
            if a:
                sz = int(a.get('b64_len') or 0)
                tk.Label(rowf, text='已上传 %s（%.0f KB，%s）' % (a.get('file_name'), sz / 1365.3,
                                                              a.get('uploaded_at') or ''),
                         bg=rowf['bg'], font=("Arial", 9)).pack(side='left', padx=6)
                ext = (a.get('file_ext') or '').lower()
                if ext in DB.IMG_EXT:
                    def _show_img(aid=a['id']):
                        blob = DB.attachment_blob(aid)
                        if not blob or not blob.get('data_b64'):
                            return
                        try:
                            raw = base64.b64decode(blob['data_b64'])
                            im = Image.open(io.BytesIO(raw)).convert('RGB')
                            im.thumbnail((640, 640))
                            t = tk.Toplevel(win)
                            t.title(blob.get('file_name') or '图片')
                            ph = ImageTk.PhotoImage(im)
                            lb = tk.Label(t, image=ph)
                            lb.image = ph
                            lb.pack()
                        except Exception as e:
                            messagebox.showwarning('打不开', str(e), parent=win)
                    make_flat_button(rowf, '👁 查看图片', _show_img, '#5c6bc0', '#3949ab').pack(side='right', padx=6, pady=3)
                else:
                    def _save(aid=a['id']):
                        blob = DB.attachment_blob(aid)
                        if not blob or not blob.get('data_b64'):
                            return
                        p = filedialog.asksaveasfilename(parent=win, title='导出附件',
                                                         initialfile=blob.get('file_name') or 'attachment.docx')
                        if not p:
                            return
                        try:
                            with open(p, 'wb') as f:
                                f.write(base64.b64decode(blob['data_b64']))
                            messagebox.showinfo('已导出', p, parent=win)
                            try:
                                os.startfile(p)
                            except Exception:
                                pass
                        except Exception as e:
                            messagebox.showerror('导出失败', str(e), parent=win)
                    make_flat_button(rowf, '💾 导出打开', _save, '#00897b', '#00695c').pack(side='right', padx=6, pady=3)
            else:
                tk.Label(rowf, text='⚠ 尚未上传', bg=rowf['bg'], fg='#c62828',
                         font=("Arial", 9)).pack(side='left', padx=6)
        for a in atts:
            if not any(r['item'] == a.get('req_item') for r in reqs):
                tk.Label(mat, text='• 其他附件：%s（%s）' % (a.get('file_name'), a.get('uploaded_at') or ''),
                         bg='white', fg='#455a64', font=("Arial", 9)).pack(anchor='w')
        if app.get('submitted_materials'):
            tk.Label(left, text='申请人补充说明：' + app['submitted_materials'], bg='#e8f5e9',
                     fg='#2e7d32', font=("Arial", 9), wraplength=480,
                     justify='left').pack(anchor='w', pady=(8, 0))

        # 右侧：人脸照片
        tk.Label(right, text='👤 申请人人脸照片', bg='white', fg=COLOR_TITLE,
                 font=("Arial", 12, "bold")).pack(anchor='w')
        face_holder = tk.Frame(right, bg='#eceff1', width=290, height=290)
        face_holder.pack(pady=6)
        face_holder.pack_propagate(False)
        fb = (app.get('face_b64') or '').strip()
        if fb:
            try:
                raw = base64.b64decode(fb)
                im = Image.open(io.BytesIO(raw)).convert('RGB')
                im.thumbnail((280, 280))
                ph = ImageTk.PhotoImage(im)
                lb = tk.Label(face_holder, image=ph, bg='#eceff1')
                lb.image = ph
                lb.pack(expand=True)
            except Exception as e:
                tk.Label(face_holder, text='照片无法显示：%s' % e, bg='#eceff1',
                         fg=COLOR_DANGER, font=("Arial", 9), wraplength=260).pack(expand=True)
        else:
            tk.Label(face_holder, text='（无申请人脸照片）', bg='#eceff1',
                     fg=COLOR_GRAY, font=("Arial", 10)).pack(expand=True)
        tk.Label(right, text='（已与户籍库全部人脸比对，无重复才会提交成功）', bg='white',
                 fg=COLOR_GRAY, font=("Arial", 9), wraplength=280, justify='left').pack(anchor='w')

    def _show_nationality_cert(self):
        """开具国籍证明（含户籍信息+所有护照），可保存 DOCX。"""
        self._clear_main()
        tk.Label(self.main_frame, text="📜 国籍证明", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=15)
        # 取当前用户户籍信息
        if self.user['type'] == 'official':
            id_number = self.user.get('id_number', '')
            name = self.user.get('name', '')
        elif self.user['type'] == 'citizen':
            id_number = self.user.get('id_number', '')
            name = self.user.get('name', '')
        else:
            messagebox.showinfo("提示", "临时账号不能开具国籍证明")
            return
        if not id_number:
            messagebox.showwarning("提示", "未识别到身份证号")
            return
        # 查户籍
        hconn = DB.get_conn(HOUSEHOLD_DB)
        hconn.row_factory = sqlite3.Row
        res = dict(hconn.execute("SELECT * FROM residents WHERE id_number=? AND name=?", (id_number, name)).fetchone() or {})
        hconn.close()
        # 查护照
        passports = DB.list_passports_for(id_number)
        # 生成防伪编号
        cert_serial = DB.issue_certificate_serial(id_number, name)
        # 扫码查验地址（二维码内容就是这个网页地址，扫开即可核对身份信息）
        verify_link = ''
        verify_note = ''
        verify_is_public = False
        try:
            import cert_service
            verify_link = cert_service.verify_url(cert_serial, res.get('name', name), id_number)
            st = cert_service.tunnel_status()
            if st.get('url'):
                verify_note = '公网地址：手机连 Wi-Fi、4G、5G 都能扫，查询直达云端实时数据'
                verify_is_public = True
            elif st.get('running'):
                verify_note = '公网通道正在建立（约 10~30 秒），稍后重新打开本页就能生成公网二维码；当前是局域网地址'
            elif st.get('err'):
                verify_note = '公网通道未接通（%s）；当前是局域网地址，手机需连同一 Wi-Fi' % st['err']
            else:
                verify_note = '公网通道未启动；当前是局域网地址，手机需连同一 Wi-Fi'
        except Exception:
            verify_link = ''
        box = tk.Frame(self.main_frame, bg='#fdfdfd', bd=1, relief=tk.SOLID)
        box.pack(padx=40, pady=10, fill='both', expand=True)
        tk.Label(box, text="蛋 国 国 籍 证 明", font=("Arial", 20, "bold"), bg='#fdfdfd').pack(pady=10)
        tk.Label(box, text=f"防伪编号：{cert_serial}", bg='#fdfdfd', fg=COLOR_GRAY,
                 font=("Arial", 9, "italic")).pack()
        info_rows = [
            ('姓名', res.get('name', name)),
            ('身份证号', res.get('id_number', id_number)),
            ('性别', res.get('gender', '—')),
            ('出生日期', res.get('birth_date', '—')),
            ('户籍地址', res.get('address', '—')),
            ('登记日期', res.get('registered_date', '—')),
            ('户籍状态', res.get('status', '—')),
        ]
        for k, v in info_rows:
            f = tk.Frame(box, bg='#fdfdfd')
            f.pack(fill='x', padx=30, pady=2)
            tk.Label(f, text=f"{k}：", bg='#fdfdfd', font=("Arial", 11, "bold"), width=12, anchor='w').pack(side='left')
            tk.Label(f, text=str(v), bg='#fdfdfd', font=("Arial", 11)).pack(side='left')
        tk.Label(box, text="—— 出入境护照信息 ——", bg='#fdfdfd', font=("Arial", 11, "bold")).pack(pady=(15, 5))
        if not passports:
            tk.Label(box, text="（该身份证号名下暂无护照）", bg='#fdfdfd', fg=COLOR_GRAY).pack()
        for p in passports:
            pf = tk.Frame(box, bg='#f0f4f8', bd=1, relief=tk.SOLID)
            pf.pack(fill='x', padx=30, pady=4)
            tk.Label(pf, text=f"护照号 {p['passport_number']}（{p['passport_type']}）", bg='#f0f4f8',
                     font=("Arial", 10, "bold")).pack(anchor='w', padx=8, pady=3)
            tk.Label(pf, text=f"签发：{p['issue_date']}　有效期至：{p['expiry_date']}　签发机关：{p['issuing_office']}　状态：{p['status']}",
                     bg='#f0f4f8', font=("Arial", 9)).pack(anchor='w', padx=8)
        # 落款
        tk.Label(box, text=f"\n签发机关：袁泓涵政府", bg='#fdfdfd',
                 font=("Arial", 12, "bold")).pack(pady=(20, 2))
        tk.Label(box, text=f"签发日期：{datetime.now().strftime('%Y-%m-%d')}", bg='#fdfdfd').pack()
        if verify_link:
            # 预览页直接画出二维码，方便当场用手机扫
            try:
                import qrcode as _qrc
                _q = _qrc.QRCode(box_size=6, border=2)
                _q.add_data(verify_link)
                _q.make(fit=True)
                _qimg = _q.make_image(fill_color='black', back_color='white').convert('RGB').resize((160, 160))
                _qphoto = ImageTk.PhotoImage(_qimg)
                _qlabel = tk.Label(box, image=_qphoto, bg='#fdfdfd')
                _qlabel.image = _qphoto
                _qlabel.pack(pady=(10, 2))
                tk.Label(box, text='扫码即可打开查验网页', bg='#fdfdfd', fg=COLOR_GRAY,
                         font=("Arial", 9)).pack()
            except Exception:
                pass
            vf = tk.Frame(box, bg='#fdfdfd')
            vf.pack(fill='x', padx=30, pady=(12, 4))
            tk.Label(vf, text=f"📱 扫码查验地址：{verify_link}", bg='#fdfdfd',
                     fg='#0b62d6' if verify_is_public else '#8a6d1f',
                     font=("Arial", 9), wraplength=560, justify='left').pack(side='left')

            def _copy_link():
                try:
                    self.main_frame.clipboard_clear()
                    self.main_frame.clipboard_append(verify_link)
                    messagebox.showinfo('已复制', '查验地址已复制到剪贴板，可在手机浏览器直接打开。')
                except Exception:
                    pass

            tk.Button(vf, text='复制', command=_copy_link, font=("Arial", 9), bd=0,
                      bg='#e8f0fe', fg='#0b62d6', activebackground='#d6e4ff',
                      cursor='hand2', padx=8, pady=2).pack(side='left', padx=8)
            if verify_note:
                tk.Label(box, text='（%s）' % verify_note,
                         bg='#fdfdfd', fg='#0b62d6' if verify_is_public else COLOR_GRAY,
                         font=("Arial", 9), wraplength=560,
                         justify='left').pack(fill='x', padx=30, pady=(0, 2))
            tk.Label(box, text="（扫码打开后表单自动填好，点「确认查询」即可核验，也可手动输入身份证号查询）",
                     bg='#fdfdfd', fg=COLOR_GRAY, font=("Arial", 9), wraplength=560,
                     justify='left').pack(fill='x', padx=30, pady=(0, 8))

        def save_docx():
            try:
                from docx import Document
                from docx.shared import Pt, Cm
                from docx.enum.text import WD_ALIGN_PARAGRAPH
            except ImportError:
                messagebox.showerror('缺少依赖',
                                     '当前解释器未安装 python-docx，无法导出 Word 文档。\n\n'
                                     '请在命令行执行：pip install python-docx qrcode[pil]')
                return
            doc = Document()
            for section in doc.sections:
                section.top_margin = Cm(2.5)
                section.bottom_margin = Cm(2.5)
            h = doc.add_paragraph()
            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = h.add_run("蛋 国 国 籍 证 明")
            run.bold = True
            run.font.size = Pt(22)
            doc.add_paragraph("")
            for k, v in info_rows:
                p = doc.add_paragraph()
                r1 = p.add_run(f"{k}：")
                r1.bold = True
                r1.font.size = Pt(12)
                r2 = p.add_run(str(v))
                r2.font.size = Pt(12)
            doc.add_paragraph("")
            ph = doc.add_paragraph()
            ph.add_run("—— 出入境护照信息 ——").bold = True
            if not passports:
                doc.add_paragraph("（该身份证号名下暂无护照）")
            else:
                table = doc.add_table(rows=1, cols=5)
                table.style = 'Light Grid Accent 1'
                hdr = table.rows[0].cells
                for i, t in enumerate(['护照号', '类型', '签发日期', '有效期至', '签发机关']):
                    hdr[i].text = t
                for p in passports:
                    row = table.add_row().cells
                    row[0].text = p['passport_number']
                    row[1].text = p['passport_type']
                    row[2].text = p['issue_date']
                    row[3].text = p['expiry_date']
                    row[4].text = p['issuing_office']
            doc.add_paragraph("")
            # 防伪编号 + 二维码
            serial_p = doc.add_paragraph()
            serial_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            sr = serial_p.add_run(f"防伪编号：{cert_serial}")
            sr.bold = True
            sr.font.size = Pt(10)
            try:
                import qrcode, tempfile, os
                qr = qrcode.QRCode(box_size=5, border=2)
                # 二维码内容 = 查验网页地址（含防伪编号/姓名/身份证号，扫码后表单自动填好）
                qr.add_data(verify_link or (f"蛋国国籍证明查验\n编号：{cert_serial}\n"
                                           f"姓名：{res.get('name', name)}\n身份证号：{id_number}"))
                qr.make(fit=True)
                img = qr.make_image(fill_color='black', back_color='white')
                qr_path = os.path.join(tempfile.gettempdir(), f'qr_{cert_serial}.png')
                img.save(qr_path)
                qp = doc.add_paragraph()
                qp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                qp.add_run().add_picture(qr_path, width=Cm(3.5))
                if verify_link:
                    tip = doc.add_paragraph()
                    tip.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    tr = tip.add_run(f"扫码查验：{verify_link}")
                    tr.font.size = Pt(8)
            except Exception as e:
                doc.add_paragraph(f"（二维码生成失败：{e}）")
            doc.add_paragraph("")
            doc.add_paragraph("")
            sign = doc.add_paragraph()
            sign.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            sign.add_run(f"签发机关：袁泓涵政府\n").bold = True
            sign.add_run(f"签发日期：{datetime.now().strftime('%Y 年 %m 月 %d 日')}")
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(defaultextension='.docx',
                                               filetypes=[('Word 文档', '*.docx')],
                                               initialfile=f'国籍证明_{name}_{id_number}.docx')
            if not path:
                return
            try:
                doc.save(path)
            except PermissionError:
                # 文件正被 Word 打开（或目录不可写）：换个文件名再存一次
                base, ext = os.path.splitext(path)
                stamp = datetime.now().strftime('%H%M%S')
                alt = None
                for i in range(1, 30):
                    cand = f'{base}_{stamp}' + ('' if i == 1 else f'_{i}') + ext
                    try:
                        doc.save(cand)
                        alt = cand
                        break
                    except PermissionError:
                        continue
                    except Exception:
                        break
                if alt:
                    messagebox.showinfo("已保存（原文件被占用）",
                                        f"目标文件可能正在 Word 里打开，已另存为：\n{alt}\n\n"
                                        "如需覆盖原文件，请先关闭 Word 中的该文档再保存一次。")
                else:
                    messagebox.showerror("保存失败",
                                         f"无法写入：\n{path}\n\n"
                                         "可能是该文件正在 Word 中打开，或该目录没有写权限。\n"
                                         "请关闭文档后重试，或换一个保存位置。")
                return
            except Exception as e:
                messagebox.showerror("保存失败", f"写入文件时出错：\n{e}")
                return
            messagebox.showinfo("已保存", f"国籍证明已保存至：\n{path}")

        make_flat_button(self.main_frame, "💾 保存为 DOCX", save_docx, COLOR_PRIMARY, COLOR_PRIMARY_HOVER).pack(pady=10)

    # ========== 居住地管理（公民选州，用于选举人团计票） ==========
    def _show_state_select(self):
        self._clear_main()
        tk.Label(self.main_frame, text="🏠 居住地管理", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=15)
        tk.Label(self.main_frame,
                 text="选择您在蛋国居住的州。您投的总统选举票将按所在州计入该州选举人团。",
                 bg='white', fg=COLOR_GRAY, wraplength=600).pack(pady=5)
        # 当前州
        hid = self.user.get('id_number', '')
        hconn = DB.get_conn(HOUSEHOLD_DB)
        hconn.row_factory = sqlite3.Row
        cur = hconn.execute("SELECT state FROM residents WHERE id_number=?", (hid,)).fetchone()
        hconn.close()
        cur_state = cur['state'] if cur else ''
        tk.Label(self.main_frame, text=f"当前居住地：{cur_state or '未登记'}",
                 bg='white', font=("Arial", 12, "bold")).pack(pady=10)
        tk.Label(self.main_frame, text="—— 各州选举人票数 ——", bg='white', fg=COLOR_GRAY).pack(pady=5)
        bar = tk.Frame(self.main_frame, bg='white')
        bar.pack(pady=5)
        for i, st in enumerate(ELECTORAL_STATES):
            card = tk.Frame(bar, bg='#f8f9fa', bd=1, relief=tk.SOLID, padx=15, pady=10)
            card.grid(row=i // 3, column=i % 3, padx=10, pady=8)
            tk.Label(card, text=st, bg='#f8f9fa', font=("Arial", 12, "bold")).pack()
            tk.Label(card, text=f"{ELECTORAL_VOTES[st]} 票", bg='#f8f9fa',
                     fg=COLOR_DANGER, font=("Arial", 14, "bold")).pack()
            chosen = tk.StringVar(value=cur_state)
            rb = tk.Radiobutton(card, text="选这里", variable=chosen, value=st, bg='#f8f9fa')
            rb.pack(pady=5)
        def save():
            st = chosen.get()
            if not st:
                messagebox.showwarning("提示", "请先选择一个州")
                return
            hconn = DB.get_conn(HOUSEHOLD_DB)
            hconn.execute("UPDATE residents SET state=?, last_update=? WHERE id_number=?",
                          (st, now_str(), hid))
            hconn.commit()
            hconn.close()
            messagebox.showinfo("已保存", f"您的居住地已更新为：{st}\n您的总统选举票将计入该州选举人团。")
            self._show_state_select()
        make_flat_button(self.main_frame, "💾 保存居住地", save, COLOR_PRIMARY, COLOR_PRIMARY_HOVER).pack(pady=15)

    # ========== 💰 国库财政 ==========
    def _show_treasury(self):
        self._clear_main()
        tk.Label(self.main_frame, text="💰 国库财政（自动月结）", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=10)
        bal = DB.treasury_balance()
        tk.Label(self.main_frame, text=f"当前国库余额：¥ {bal:,.2f}",
                 font=("Arial", 16, "bold"), bg='white',
                 fg=COLOR_SUCCESS if bal > 0 else COLOR_DANGER).pack(pady=8)
        tk.Label(self.main_frame,
                 text="ℹ️ 每月 1 号系统自动收税（公民 ¥50/人）并发薪（按级别），无需手动操作。",
                 bg='white', fg=COLOR_GRAY, font=("Arial", 10)).pack(pady=5)
        # 流水账单（只读）
        tk.Label(self.main_frame, text="—— 国库流水账单 ——", bg='white', fg=COLOR_GRAY).pack(pady=8)
        for row in DB.treasury_log():
            sign = '+' if row['amount'] > 0 else ''
            color = COLOR_SUCCESS if row['amount'] > 0 else COLOR_DANGER
            tk.Label(self.main_frame,
                     text=f"[{row['txn_type']}] {sign}{row['amount']:,.2f}  余额 ¥{row['balance_after']:,.2f}  {row['note']}  {row['created_at']}",
                     bg='white', fg=color, font=("Arial", 10)).pack(anchor='w', padx=20)

    # ========== ⚖️ 违宪审查 ==========
    def _show_constitutional_cases(self):
        self._clear_main()
        tk.Label(self.main_frame, text="⚖️ 违宪审查（最高法院）", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=10)
        # 提起新案
        frm = tk.LabelFrame(self.main_frame, text="提起违宪诉讼", bg='white', padx=10, pady=5)
        frm.pack(pady=8, fill='x', padx=20)
        tk.Label(frm, text="目标类型:", bg='white').grid(row=0, column=0, padx=5, pady=3)
        target_type = ttk.Combobox(frm, values=['executive_order', 'law'], width=15)
        target_type.grid(row=0, column=1, padx=5)
        target_type.set('executive_order')
        tk.Label(frm, text="目标ID:", bg='white').grid(row=0, column=2, padx=5)
        target_id = tk.Entry(frm); target_id.grid(row=0, column=3, padx=5)
        tk.Label(frm, text="理由:", bg='white').grid(row=1, column=0, padx=5, pady=3)
        reason = tk.Entry(frm, width=60); reason.grid(row=1, column=1, columnspan=3, padx=5)
        def file_case():
            try:
                tid = int(target_id.get())
            except:
                messagebox.showwarning("提示", "目标ID必须是数字"); return
            if not reason.get().strip():
                messagebox.showwarning("提示", "请填写理由"); return
            cid, cno = DB.file_constitutional_case(target_type.get(), tid,
                self.user.get('name', '公民'), self.user.get('id_number', ''), reason.get())
            messagebox.showinfo("已立案", f"案件编号 {cno}（id={cid}）")
            self._show_constitutional_cases()
        make_flat_button(frm, "📨 提交诉讼", file_case, COLOR_PRIMARY, COLOR_PRIMARY_HOVER).grid(row=2, column=1, pady=5)
        # 案件列表
        tk.Label(self.main_frame, text="—— 案件列表 ——", bg='white', fg=COLOR_GRAY).pack(pady=8)
        for row in DB.list_constitutional_cases():
            card = tk.Frame(self.main_frame, bg='#f8f9fa', bd=1, relief=tk.SOLID, padx=10, pady=5)
            card.pack(fill='x', padx=20, pady=3)
            tk.Label(card, text=f"#{row['case_no']}  [{row['status']}]  {row['target_type']}#{row['target_id']}",
                     bg='#f8f9fa', font=("Arial", 11, "bold")).pack(anchor='w')
            tk.Label(card, text=f"原告: {row['plaintiff']}  理由: {row['reason']}",
                     bg='#f8f9fa', fg=COLOR_GRAY, font=("Arial", 10)).pack(anchor='w')
            if row['status'] == '待审理':
                def rule(rid=row['id']):
                    if not messagebox.askyesno("裁定", "裁定违宪？（选否则合宪）"):
                        ok, msg = DB.rule_constitutional_case(rid, '合宪', '庭审后认为符合宪法',
                                                              self.user.get('official_id', 0))
                    else:
                        ok, msg = DB.rule_constitutional_case(rid, '违宪', '与宪法条款冲突',
                                                              self.user.get('official_id', 0))
                    messagebox.showinfo("裁定", msg)
                    self._show_constitutional_cases()
                make_flat_button(card, "⚖️ 裁定", rule, COLOR_WARNING, '#d35400').pack(anchor='e', pady=3)

    # ========== 📰 新闻联播 ==========
    def _show_news(self):
        self._clear_main()
        tk.Label(self.main_frame, text="📰 新闻联播", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=10)
        for row in DB.list_news(30):
            color = COLOR_SUCCESS if row['impact_direction'] == '正面' else (
                COLOR_DANGER if row['impact_direction'] == '负面' else COLOR_GRAY)
            card = tk.Frame(self.main_frame, bg='#f8f9fa', bd=1, relief=tk.SOLID, padx=10, pady=5)
            card.pack(fill='x', padx=20, pady=3)
            tk.Label(card, text=f"[{row['category']}] {row['title']}",
                     bg='#f8f9fa', font=("Arial", 11, "bold")).pack(anchor='w')
            tk.Label(card, text=row['content'], bg='#f8f9fa', fg=COLOR_TITLE,
                     font=("Arial", 10), wraplength=700, justify='left').pack(anchor='w')
            tk.Label(card, text=f"影响: {row['impact_direction']} {row['impact_value']:+.1f}  范围: {row['affected_state']}  {row['created_at']}",
                     bg='#f8f9fa', fg=color, font=("Arial", 9)).pack(anchor='w')

    # ========== 🗳️ 公投中心（官员） ==========
    def _show_referenda_admin(self):
        self._clear_main()
        tk.Label(self.main_frame, text="🗳️ 公投中心", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=10)
        tk.Label(self.main_frame, text="—— 请愿书 ——", bg='white', fg=COLOR_GRAY).pack(pady=5)
        for row in DB.list_petitions():
            card = tk.Frame(self.main_frame, bg='#f8f9fa', bd=1, relief=tk.SOLID, padx=10, pady=5)
            card.pack(fill='x', padx=20, pady=3)
            tk.Label(card, text=f"{row['title']}  [{row['status']}]  签名 {row['signatures_count']}/{row['signatures_needed']}",
                     bg='#f8f9fa', font=("Arial", 11, "bold")).pack(anchor='w')
            tk.Label(card, text=row['description'], bg='#f8f9fa', fg=COLOR_GRAY).pack(anchor='w')
        tk.Label(self.main_frame, text="—— 公投 ——", bg='white', fg=COLOR_GRAY).pack(pady=10)
        for row in DB.list_referenda():
            card = tk.Frame(self.main_frame, bg='#f8f9fa', bd=1, relief=tk.SOLID, padx=10, pady=5)
            card.pack(fill='x', padx=20, pady=3)
            tk.Label(card, text=f"{row['title']}  [{row['status']}]  赞成{row['yes_votes']} 反对{row['no_votes']}",
                     bg='#f8f9fa', font=("Arial", 11, "bold")).pack(anchor='w')
            if row['status'] == '投票中':
                def close(rid=row['id']):
                    ok, msg = DB.close_referendum(rid)
                    messagebox.showinfo("结束公投", msg)
                    self._show_referenda_admin()
                make_flat_button(card, "🏁 结束并计票", close, COLOR_DANGER, COLOR_DANGER_HOVER).pack(anchor='e')

    # ========== ✍️ 请愿签名（公民） ==========
    def _show_petitions_citizen(self):
        self._clear_main()
        tk.Label(self.main_frame, text="✍️ 请愿签名", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=10)
        # 发起新请愿
        frm = tk.LabelFrame(self.main_frame, text="发起新请愿", bg='white', padx=10, pady=5)
        frm.pack(pady=8, fill='x', padx=20)
        tk.Label(frm, text="标题:", bg='white').grid(row=0, column=0, padx=5, pady=3)
        t = tk.Entry(frm, width=50); t.grid(row=0, column=1, padx=5)
        tk.Label(frm, text="描述:", bg='white').grid(row=1, column=0, padx=5, pady=3)
        d = tk.Entry(frm, width=50); d.grid(row=1, column=1, padx=5)
        def create():
            if not t.get().strip():
                messagebox.showwarning("提示", "请填写标题"); return
            pid = DB.create_petition(t.get(), d.get(),
                self.user.get('id_number', ''), self.user.get('name', '公民'))
            messagebox.showinfo("已发起", f"请愿 #{pid} 已创建")
            self._show_petitions_citizen()
        make_flat_button(frm, "📝 发起", create, COLOR_PRIMARY, COLOR_PRIMARY_HOVER).grid(row=2, column=1, pady=5)
        # 列表
        for row in DB.list_petitions():
            card = tk.Frame(self.main_frame, bg='#f8f9fa', bd=1, relief=tk.SOLID, padx=10, pady=5)
            card.pack(fill='x', padx=20, pady=3)
            tk.Label(card, text=f"{row['title']}  [{row['status']}]  {row['signatures_count']}/{row['signatures_needed']}",
                     bg='#f8f9fa', font=("Arial", 11, "bold")).pack(anchor='w')
            tk.Label(card, text=row['description'], bg='#f8f9fa', fg=COLOR_GRAY).pack(anchor='w')
            if row['status'] == '收集签名':
                def sign(pid=row['id']):
                    ok, msg = DB.sign_petition(pid, self.user.get('id_number', ''), self.user.get('name', ''))
                    messagebox.showinfo("签名", msg)
                    self._show_petitions_citizen()
                make_flat_button(card, "✍️ 签名", sign, COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(anchor='e')

    # ========== 🗳️ 公投投票（公民） ==========
    def _show_referenda_citizen(self):
        self._clear_main()
        tk.Label(self.main_frame, text="🗳️ 公投投票", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=10)
        for row in DB.list_referenda():
            card = tk.Frame(self.main_frame, bg='#f8f9fa', bd=1, relief=tk.SOLID, padx=10, pady=5)
            card.pack(fill='x', padx=20, pady=3)
            tk.Label(card, text=f"{row['title']}  [{row['status']}]",
                     bg='#f8f9fa', font=("Arial", 11, "bold")).pack(anchor='w')
            tk.Label(card, text=row['description'], bg='#f8f9fa', fg=COLOR_GRAY).pack(anchor='w')
            tk.Label(card, text=f"当前 赞成{row['yes_votes']} : 反对{row['no_votes']}",
                     bg='#f8f9fa', fg=COLOR_TITLE).pack(anchor='w')
            if row['status'] == '投票中':
                bf = tk.Frame(card, bg='#f8f9fa'); bf.pack(anchor='e')
                def vote(rid=row['id'], choice='赞成'):
                    ok, msg = DB.vote_referendum(rid, self.user.get('id_number', ''),
                                                 self.user.get('name', ''), choice)
                    messagebox.showinfo("投票", msg)
                    self._show_referenda_citizen()
                make_flat_button(bf, "👍 赞成", lambda: vote(row['id'], '赞成'),
                                 COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(side='left', padx=3)
                make_flat_button(bf, "👎 反对", lambda: vote(row['id'], '反对'),
                                 COLOR_DANGER, COLOR_DANGER_HOVER).pack(side='left', padx=3)

    # ========== 公职申请（民众→政府） ==========
    def _show_job_apply(self):
        self._clear_main()
        tk.Label(self.main_frame, text="🏛️ 申请政府公职", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=15)
        tk.Label(self.main_frame, text="选择你想申请的部门与职位（总统/副总统职位由选举产生，不在此申请）。",
                 bg='white', fg=COLOR_GRAY).pack()
        form = tk.Frame(self.main_frame, bg='white')
        form.pack(pady=20)
        tk.Label(form, text="部门：", bg='white', font=("Arial", 11)).grid(row=0, column=0, padx=10, pady=10, sticky='e')
        dept_var = tk.StringVar()
        depts = DB.list_departments()
        dept_box = ttk.Combobox(form, textvariable=dept_var, values=depts, width=28, state='readonly')
        dept_box.grid(row=0, column=1, padx=10, pady=10)
        tk.Label(form, text="职位：", bg='white', font=("Arial", 11)).grid(row=1, column=0, padx=10, pady=10, sticky='e')
        pos_var = tk.StringVar()
        pos_box = ttk.Combobox(form, textvariable=pos_var, values=[], width=28, state='readonly')
        pos_box.grid(row=1, column=1, padx=10, pady=10)

        def on_dept_change(e=None):
            positions = DB.list_positions_for_department(dept_var.get())
            pos_box['values'] = positions
            pos_var.set('')
        dept_box.bind('<<ComboboxSelected>>', on_dept_change)

        tk.Label(form, text="申请理由：", bg='white', font=("Arial", 11)).grid(row=2, column=0, padx=10, pady=10, sticky='ne')
        reason_text = tk.Text(form, width=32, height=5, font=("Arial", 10))
        reason_text.grid(row=2, column=1, padx=10, pady=10)

        def submit():
            if not dept_var.get() or not pos_var.get():
                messagebox.showwarning("提示", "请选择部门和职位")
                return
            reason = reason_text.get('1.0', 'end').strip()
            ok, msg = DB.submit_job_app(self.user['id_number'], self.user['name'],
                                        dept_var.get(), pos_var.get(), reason)
            if not ok:
                messagebox.showerror("错误", msg)
            else:
                messagebox.showinfo("已提交", msg)
                self._show_my_job_apps()
        make_flat_button(self.main_frame, "✅ 提交申请", submit, COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(pady=10)

    def _show_my_job_apps(self):
        self._clear_main()
        tk.Label(self.main_frame, text="📋 我的公职申请", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=15)
        apps = DB.list_my_job_apps(self.user['id_number'])
        if not apps:
            tk.Label(self.main_frame, text="暂无申请记录。", bg='white', fg=COLOR_GRAY).pack(pady=30)
            return
        cols = ('id', 'dept', 'pos', 'status', 'reviewer', 'note', 'term', 'created')
        tree = ttk.Treeview(self.main_frame, columns=cols, show='headings', height=12)
        for c, t, w in [('id', 'ID', 50), ('dept', '部门', 140), ('pos', '职位', 140),
                        ('status', '状态', 90), ('reviewer', '审核人', 90),
                        ('note', '审核备注', 200), ('term', '任期', 180), ('created', '提交时间', 150)]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor='center')
        tree.pack(fill='both', expand=True, padx=15, pady=5)
        for a in apps:
            term = f"{a['term_start']}~{a['term_end']}" if a['term_start'] else '—'
            tree.insert('', 'end', values=(a['id'], a['department'], a['position_name'],
                                           a['status'], a['reviewer_name'] or '—',
                                           a['reviewer_note'] or '—', term, a['created_at']))

    def _show_job_review(self):
        self._clear_main()
        tk.Label(self.main_frame, text="📋 公职申请审核", font=("Arial", 18, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(pady=15)
        apps = DB.list_job_apps_for_reviewer(self.user)
        if not apps:
            tk.Label(self.main_frame, text="暂无待审核申请。", bg='white', fg=COLOR_GRAY).pack(pady=30)
            return
        cols = ('id', 'name', 'idnum', 'dept', 'pos', 'reason', 'created')
        tree = ttk.Treeview(self.main_frame, columns=cols, show='headings', height=10)
        for c, t, w in [('id', 'ID', 50), ('name', '申请人', 100), ('idnum', '身份证号', 160),
                        ('dept', '部门', 140), ('pos', '职位', 140),
                        ('reason', '理由', 280), ('created', '提交时间', 150)]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor='center')
        tree.pack(fill='both', expand=True, padx=15, pady=5)
        for a in apps:
            tree.insert('', 'end', values=(a['id'], a['applicant_name'], a['applicant_id_number'],
                                           a['department'], a['position_name'],
                                           a['reason'][:60], a['created_at']))

        def do_review(approve):
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("提示", "请先选中一条申请")
                return
            aid = int(tree.item(sel[0], 'values')[0])
            note = ''
            term_start = term_end = ''
            if not approve:
                note = simpledialog.askstring("拒绝原因", "请输入拒绝原因（可空）：", parent=self.root) or ''
            else:
                term_start = simpledialog.askstring("任期开始", "请输入任期开始日期（YYYY-MM-DD）：", parent=self.root) or ''
                if not term_start:
                    return
                term_end = simpledialog.askstring("任期结束", "请输入任期结束日期（YYYY-MM-DD）：", parent=self.root) or ''
            ok, msg = DB.review_job_app(aid, approve, self.user['id'], self.user['name'], note, term_start, term_end)
            if ok:
                messagebox.showinfo("完成", msg)
            else:
                messagebox.showerror("错误", msg)
            self._show_job_review()

        bar = tk.Frame(self.main_frame, bg='white')
        bar.pack(fill='x', padx=15, pady=10)
        make_flat_button(bar, "✅ 批准并任命", lambda: do_review(True), COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(side='left', padx=5)
        make_flat_button(bar, "❌ 拒绝", lambda: do_review(False), COLOR_DANGER, COLOR_DANGER_HOVER).pack(side='left', padx=5)

    # ---------- 权限辅助（官员端） ----------
    def _pinfo(self):
        """当前官员的权限信息（按会话缓存；重新登录后刷新）。"""
        if not getattr(self, '_perm_cache', None):
            self._perm_cache = DB.official_perm(self.user)
        return self._perm_cache

    def _can(self, perm_key):
        return DB.can(self.user, perm_key)

    def _guard(self, perm_key, action_name):
        """无权限时弹窗并返回 False；有权限返回 True。"""
        if self._can(perm_key):
            return True
        messagebox.showwarning('无操作权限',
                               f'您的账号没有“{action_name}”权限。\n'
                               f'当前由部门层级与职级决定默认权限，如需开通请联系管理员在“权限管理”中调整。')
        return False

    def _refresh_status(self):
        """刷新顶部状态栏（含未读消息数）"""
        try:
            if self.user['type'] == 'admin':
                rid = str(self.user['id'])
            else:
                rid = self.user['id_number']
            unread = DB.unread_count(self.user['type'], rid)
            role_text = "管理员" if self.user['type'] == 'admin' else ("政府官员" if self.user['type'] == 'official' else "普通民众")
            info = f"当前用户：{self.user['name']}（{role_text}）"
            if self.user['type'] == 'official':
                info += f" | {self.user.get('department', '')} · {self.user.get('position', '')}"
            if unread:
                info += f"    📨 未读消息 {unread} 条"
            self.status_info_label.config(text=info)
        except:
            pass

    # ========== 仪表盘 ==========
    def _show_dashboard(self):
        if self.user['type'] == 'temp':
            self._show_temp_status()
            return
        self._clear_main()
        self._refresh_status()
        make_page_title(self.main_frame, "🏛️ 蛋国政务服务一体化平台 - 总览").pack(pady=20)

        # 登录后显示当前已应用的版本号与更新内容
        try:
            _cur = DB.current_update()
            if _cur and _cur.get('version'):
                _vtxt = '当前版本 v%s' % _cur['version']
                if _cur.get('note'):
                    _vtxt += '：' + (_cur['note'] or '').replace('\n', ' ')[:110]
                tk.Label(self.main_frame, text='🔄 ' + _vtxt, font=('Arial', 10),
                         bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 6))
        except Exception:
            pass


        conn = DB.get_conn(GOV_DB)
        if self.user['type'] == 'admin':
            stats = [
                ("在任官员", str(conn.execute("SELECT COUNT(*) FROM officials WHERE status='在职'").fetchone()[0]), COLOR_PRIMARY, "👥"),
                ("待办办件", str(conn.execute("SELECT COUNT(*) FROM service_cases WHERE status IN ('待受理','受理中','办理中')").fetchone()[0]), COLOR_WARNING, "🗂️"),
                ("待处理举报", str(conn.execute("SELECT COUNT(*) FROM reports WHERE status='待处理'").fetchone()[0]), COLOR_DANGER, "🚨"),
                ("待回复咨询", str(conn.execute("SELECT COUNT(*) FROM consultations WHERE status='待回复'").fetchone()[0]), COLOR_PURPLE, "💬"),
                ("已办结办件", str(conn.execute("SELECT COUNT(*) FROM service_cases WHERE status='已办结'").fetchone()[0]), COLOR_SUCCESS, "✅"),
            ]
        elif self.user['type'] == 'official':
            dept = self.user['department']
            oid = self.user['id_number']
            stats = [
                ("本部门待办办件", str(conn.execute("SELECT COUNT(*) FROM service_cases WHERE department=? AND status IN ('待受理','受理中','办理中')", (dept,)).fetchone()[0]), COLOR_WARNING, "🗂️"),
                ("待审批文件", str(conn.execute("SELECT COUNT(*) FROM approval_files WHERE status='待审批' AND (target_department=? OR target_department IS NULL OR target_department='')", (dept,)).fetchone()[0]), COLOR_PRIMARY, "📋"),
                ("收到建议", str(conn.execute("SELECT COUNT(*) FROM suggestions WHERE official_id=?", (oid,)).fetchone()[0]), COLOR_SUCCESS, "💬"),
                ("待回复咨询", str(conn.execute("SELECT COUNT(*) FROM consultations WHERE department=? AND status='待回复'", (dept,)).fetchone()[0]), COLOR_PURPLE, "💬"),
            ]
        else:
            cid = self.user['id_number']
            stats = [
                ("我的办件", str(conn.execute("SELECT COUNT(*) FROM service_cases WHERE applicant_id=?", (cid,)).fetchone()[0]), COLOR_PRIMARY, "📋"),
                ("办理中", str(conn.execute("SELECT COUNT(*) FROM service_cases WHERE applicant_id=? AND status IN ('待受理','受理中','办理中')", (cid,)).fetchone()[0]), COLOR_WARNING, "⏳"),
                ("我的举报", str(conn.execute("SELECT COUNT(*) FROM reports WHERE reporter_id=?", (cid,)).fetchone()[0]), COLOR_DANGER, "🚨"),
                ("政务公告", str(conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]), COLOR_WARNING, "📢"),
            ]
        conn.close()

        stats_frame = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        stats_frame.pack(fill=tk.X, padx=30, pady=10)
        for i, (title, value, color, icon) in enumerate(stats):
            card = tk.Frame(stats_frame, relief=tk.RAISED, bd=2, bg=COLOR_CONTENT_BG, width=190, height=100)
            card.grid(row=0, column=i, padx=12, pady=10)
            card.grid_propagate(False)
            tk.Label(card, text=icon, font=("Arial", 24), bg=COLOR_CONTENT_BG).pack(pady=5)
            tk.Label(card, text=title, font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack()
            tk.Label(card, text=value, font=("Arial", 14, "bold"), bg=COLOR_CONTENT_BG, fg=color).pack()

        # 最新公告
        list_frame = tk.LabelFrame(self.main_frame, text="最新政务公告", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT 5").fetchall()
        conn.close()
        if rows:
            for r in rows:
                item = tk.Frame(list_frame, bg='#f8f9fa')
                item.pack(fill='x', padx=5, pady=3)
                tk.Label(item, text=f"[{r['priority']}] {r['title']}", font=("Arial", 11, "bold"),
                         bg='#f8f9fa', fg=COLOR_TITLE).pack(anchor='w', padx=10, pady=(5, 0))
                tk.Label(item, text=r['content'][:120] + ('...' if len(r['content']) > 120 else ''),
                         font=("Arial", 10), bg='#f8f9fa', fg='#666', wraplength=1000, justify='left').pack(anchor='w', padx=10, pady=(0, 3))
                tk.Label(item, text=f"发布人：{r['publisher']} | {r['created_at']}", font=("Arial", 9),
                         bg='#f8f9fa', fg='#999').pack(anchor='e', padx=10, pady=(0, 5))
        else:
            tk.Label(list_frame, text="暂无公告", font=("Arial", 14), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=30)
    # ========== 通用详情弹窗 ==========
    def _popup_detail(self, title, info_pairs, content_label, content_text, size='680x580', extra=None):
        """通用只读详情弹窗。extra 为可选的 (按钮文字, 回调) 列表"""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry(size)
        win.transient(self.root)
        win.grab_set()
        info_f = tk.Frame(win, bg='white')
        info_f.pack(fill='x', padx=15, pady=10)
        for i, (k, v) in enumerate(info_pairs):
            tk.Label(info_f, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e', bg='white').grid(
                row=i // 2, column=(i % 2) * 2, sticky='e', padx=5, pady=3)
            tk.Label(info_f, text=str(v if v is not None else '-'), font=("Arial", 10), anchor='w',
                     bg='white', wraplength=270).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky='w', pady=3)
        tk.Label(win, text=content_label, font=("Arial", 11, "bold")).pack(anchor='w', padx=15)
        txt = scrolledtext.ScrolledText(win, font=("Arial", 11), height=10, wrap='word')
        txt.pack(fill='both', expand=True, padx=15, pady=5)
        txt.insert('1.0', content_text)
        txt.config(state='disabled')
        if extra:
            op_f = tk.Frame(win)
            op_f.pack(fill='x', padx=15, pady=10)
            for text, cmd, color, hover in extra:
                make_flat_button(op_f, text, cmd, color, hover, width=12, height=1).pack(side='left', padx=6)
        return win

    # ========== 民众：预约办理弹窗（通用，可关联办件） ==========
    def _open_appointment_dialog(self, item_id=None, item_name=None, department=None, case_no=None):
        """在线预约弹窗。传入 case_no 时即"为办件预约办理"；否则为通用事项预约。"""
        win = tk.Toplevel(self.root)
        win.title(f'预约办理 - {case_no}' if case_no else '在线预约')
        win.geometry('600x430')
        win.transient(self.root)
        win.grab_set()
        conn = DB.get_conn(GOV_DB)
        items = conn.execute("SELECT id,name,department FROM service_items WHERE is_active=1 ORDER BY name").fetchall()
        conn.close()
        item_names = [f"{i['name']}（{i['department']}）" for i in items]
        if not items:
            messagebox.showwarning('提示', '暂无可用预约事项')
            win.destroy()
            return

        if case_no:
            tk.Label(win, text=f"正在为办件 {case_no} 预约办理", font=("Arial", 12, "bold"),
                     fg=COLOR_PRIMARY).pack(pady=(12, 2))
        tk.Label(win, text="预约后请按预约时间携带材料到政务大厅窗口办理。", font=("Arial", 10),
                 fg=COLOR_TEXT_GRAY).pack()

        f = tk.Frame(win, bg='white')
        f.pack(fill='x', padx=25, pady=8)
        tk.Label(f, text="预约事项：", font=("Arial", 11), bg='white').grid(row=0, column=0, sticky='e', pady=8, padx=5)
        item_var = tk.StringVar()
        if item_name and department:
            pre = f"{item_name}（{department}）"
            if pre in item_names:
                item_var.set(pre)
        item_cb = ttk.Combobox(f, textvariable=item_var, values=item_names, font=("Arial", 11),
                               width=34, state='readonly')
        item_cb.grid(row=0, column=1, pady=8, sticky='w')
        # 确保变量与下拉框双向同步
        item_cb.bind('<<ComboboxSelected>>', lambda e: None)

        tk.Label(f, text="预约日期：", font=("Arial", 11), bg='white').grid(row=1, column=0, sticky='e', pady=8, padx=5)
        date_e = tk.Entry(f, font=("Arial", 11), width=20)
        date_e.grid(row=1, column=1, pady=8, sticky='w')
        tk.Label(f, text="(格式：2026-08-28)", font=("Arial", 9), fg=COLOR_TEXT_GRAY, bg='white').grid(
            row=1, column=2, sticky='w', padx=5)

        tk.Label(f, text="预约时段：", font=("Arial", 11), bg='white').grid(row=2, column=0, sticky='e', pady=8, padx=5)
        slot_var = tk.StringVar(value=APPT_SLOTS[0])
        ttk.Combobox(f, textvariable=slot_var, values=APPT_SLOTS, font=("Arial", 11),
                     width=12, state='readonly').grid(row=2, column=1, pady=8, sticky='w')

        def submit():
            it_name = item_var.get().strip()
            if not it_name:
                messagebox.showwarning('提示', '请选择预约事项')
                return
            date = date_e.get().strip()
            if not date:
                messagebox.showwarning('提示', '请填写预约日期')
                return
            try:
                idx = item_names.index(it_name)
            except ValueError:
                messagebox.showwarning('提示', '请选择有效的预约事项')
                return
            it = items[idx]
            appt_no = gen_no('YY')
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO service_appointments (appt_no,item_id,item_name,applicant_id,
                applicant_name,applicant_phone,appt_date,appt_time,department,status,remark,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (appt_no, it['id'], it['name'], self.user['id_number'], self.user['name'], '',
                 date, slot_var.get(), it['department'], '已预约',
                 f'关联办件：{case_no}' if case_no else '', now_str()))
            conn.commit()
            conn.close()
            note = f'（办件{case_no}）' if case_no else ''
            DB.log('citizen', self.user['id_number'], self.user['name'], '办事预约',
                   f'预约事项《{it["name"]}》{date} {slot_var.get()}{note}')
            DB.send_message('citizen', self.user['id_number'], '预约成功通知',
                            f'您已成功预约《{it["name"]}》，时间：{date} {slot_var.get()}，预约号：{appt_no}{note}')
            messagebox.showinfo('预约成功',
                                f'预约成功！\n\n预约号：{appt_no}\n事项：{it["name"]}\n时间：{date} {slot_var.get()}\n请按时到场办理。')
            win.destroy()

        make_flat_button(win, '📅 提交预约', submit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=16).pack(pady=10)

    # ========== 管理员：官员任免（选项卡：添加 / 管理） ==========
    def _show_official_mgmt(self):
        if self.user['type'] == 'official':
            if (self.user.get('position') or '') not in ('总统', '副总统'):
                messagebox.showwarning('提示', '仅管理员、总统或副总统可进行官员任免。')
                return
            if not self._can('appoint_official'):
                messagebox.showwarning('无操作权限',
                                       '您的账号没有“官员任免”权限。\n'
                                       '当前由部门层级与职级决定默认权限，如需开通请联系管理员在“权限管理”中调整。')
                return
        self._clear_main()
        make_page_title(self.main_frame, "👤 官员任免管理").pack(pady=20)
        nb = ttk.Notebook(self.main_frame)
        nb.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        tab_add = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        tab_mgmt = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        nb.add(tab_add, text="➕ 任命官员")
        nb.add(tab_mgmt, text="⚙️ 官员管理")
        self._build_add_official(tab_add)
        self._build_manage_officials(tab_mgmt)

    # ---------- 任命官员 ----------
    def _build_add_official(self, parent):
        form_frame = tk.LabelFrame(parent, text="官员任命信息", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=20, pady=15)
        form_frame.pack(pady=15, padx=50, fill=tk.X)

        tk.Label(form_frame, text="身份证号：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=0, column=0, padx=10, pady=10, sticky=tk.E)
        id_e = make_groove_entry(form_frame, width=30)
        id_e.grid(row=0, column=1, padx=10, pady=10)

        tk.Label(form_frame, text="姓　名：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=0, column=2, padx=10, pady=10, sticky=tk.E)
        name_e = make_groove_entry(form_frame, width=20)
        name_e.grid(row=0, column=3, padx=10, pady=10, sticky=tk.W)

        verify_result = tk.StringVar(value='')
        verify_label = tk.Label(form_frame, textvariable=verify_result, font=("Arial", 11), bg=COLOR_CONTENT_BG)
        verify_label.grid(row=1, column=0, columnspan=5, pady=5)
        resident_info = {}

        def verify():
            nonlocal resident_info
            idn = id_e.get().strip()
            nm = name_e.get().strip()
            if not idn or not nm:
                messagebox.showwarning('提示', '请输入身份证号和姓名')
                return
            info = DB.verify_household(idn, nm)
            if info:
                resident_info = info
                verify_result.set(f"✅ 户籍系统核对通过：{info['name']} | {info.get('gender', '')} | {info.get('birth_date', '')} | {info.get('address', '')}")
                verify_label.config(fg=COLOR_SUCCESS)
                conn = DB.get_conn(GOV_DB)
                exist = conn.execute("SELECT * FROM officials WHERE id_number=?", (idn,)).fetchone()
                conn.close()
                if exist:
                    verify_result.set(f"⚠️ 该人员已是政府官员（{exist['position']}），状态：{exist['status']}")
                    verify_label.config(fg=COLOR_WARNING)
            else:
                resident_info = {}
                verify_result.set('❌ 户籍系统未找到该身份信息，请核对')
                verify_label.config(fg=COLOR_DANGER)

        tk.Button(form_frame, text="🔍 核对户籍", command=verify, width=12, bg=COLOR_PRIMARY, fg='white',
                  font=("Arial", 10), relief=tk.FLAT, cursor='hand2').grid(row=0, column=4, padx=10, pady=10)

        tk.Label(form_frame, text="部　门：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=2, column=0, padx=10, pady=10, sticky=tk.E)
        _depts = DB.active_departments()
        dept_var = tk.StringVar(value=_depts[0] if _depts else DEPARTMENTS[0])
        dept_cb = ttk.Combobox(form_frame, textvariable=dept_var, values=_depts, font=("Arial", 11),
                               width=20, state='readonly')
        dept_cb.grid(row=2, column=1, padx=10, pady=10)

        tk.Label(form_frame, text="职　务：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=2, column=2, padx=10, pady=10, sticky=tk.E)
        pos_var = tk.StringVar()
        pos_cb = ttk.Combobox(form_frame, textvariable=pos_var, values=[], font=("Arial", 11),
                              width=15, state='readonly')
        pos_cb.grid(row=2, column=3, padx=10, pady=10, sticky=tk.W)

        def refresh_positions(_=None):
            """部门变化 -> 职务下拉联动（仅显示该部门可任职务）"""
            dept = dept_var.get()
            opts = DB.positions_for_dept(dept) if dept else DB.active_positions()
            # 总统/副总统只能由大选产生，不出现在任命下拉
            opts = [o for o in opts if o not in ('总统', '副总统')]
            pos_cb.configure(values=opts)
            cur = pos_var.get()
            if cur in opts:
                return
            # 保留原选择习惯：默认取列表最后一项（若含“总统”等高级职务），否则取第一项
            pos_var.set(opts[-1] if opts else '')
        dept_cb.bind('<<ComboboxSelected>>', refresh_positions)
        refresh_positions()

        tk.Label(form_frame, text="级　别：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=3, column=0, padx=10, pady=10, sticky=tk.E)
        rank_var = tk.StringVar(value=RANKS[0])
        ttk.Combobox(form_frame, textvariable=rank_var, values=RANKS, font=("Arial", 11),
                     width=20, state='readonly').grid(row=3, column=1, padx=10, pady=10)

        tk.Label(form_frame, text="联系电话：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=3, column=2, padx=10, pady=10, sticky=tk.E)
        phone_e = make_groove_entry(form_frame, width=18)
        phone_e.grid(row=3, column=3, padx=10, pady=10, sticky=tk.W)

        # ---- 任期输入（新需求）：起任日 / 卸任日 ----
        _today = datetime.now().strftime('%Y-%m-%d')
        _default_end = (datetime.now() + timedelta(days=365 * 2)).strftime('%Y-%m-%d')
        tk.Label(form_frame, text="起任日期：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=4, column=0, padx=10, pady=10, sticky=tk.E)
        ts_e = make_groove_entry(form_frame, width=14)
        ts_e.insert(0, _today)
        ts_e.grid(row=4, column=1, padx=10, pady=10, sticky=tk.W)
        tk.Label(form_frame, text="卸任日期：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=4, column=2, padx=10, pady=10, sticky=tk.E)
        te_e = make_groove_entry(form_frame, width=14)
        te_e.insert(0, _default_end)
        te_e.grid(row=4, column=3, padx=10, pady=10, sticky=tk.W)
        tk.Label(form_frame, text="格式 YYYY-MM-DD，任期届满自动卸任；任命后其首次登录须签署任职协议",
                 font=("Arial", 9), fg=COLOR_TEXT_GRAY, bg=COLOR_CONTENT_BG).grid(
            row=5, column=0, columnspan=5, pady=2)

        def _valid_date(s):
            try:
                datetime.strptime(s.strip(), '%Y-%m-%d')
                return True
            except Exception:
                return False

        def add_official():
            if not resident_info:
                messagebox.showwarning('提示', '请先完成户籍系统身份核对')
                return
            if not pos_var.get():
                messagebox.showwarning('提示', '请选择职务')
                return
            if not DB.validate_position_dept(pos_var.get(), dept_var.get()):
                messagebox.showwarning('职务与部门不匹配',
                                       f'职务“{pos_var.get()}”不允许在“{dept_var.get()}”任职\n'
                                       f'（可先在【职务管理】设置该职务的所属部门，或在【部门管理】绑定职位）')
                return
            ts, te = ts_e.get().strip(), te_e.get().strip()
            if not ts or not _valid_date(ts):
                messagebox.showwarning('提示', '请按 YYYY-MM-DD 格式填写起任日期')
                return
            if not te or not _valid_date(te):
                messagebox.showwarning('提示', '请按 YYYY-MM-DD 格式填写卸任日期')
                return
            if te < ts:
                messagebox.showwarning('提示', '卸任日期不能早于起任日期')
                return
            # 职务唯一名额：总统/副总统/各部部长全国唯一；处长每部门最多一名
            occ, holder = DB.position_slot_occupied(pos_var.get(), dept_var.get())
            if occ:
                messagebox.showwarning('职务名额已满',
                                       f'职务“{pos_var.get()}”已有在职担任者：{holder}\n'
                                       f'（总统、副总统、各部部长、处长同一时间只能一人担任）')
                return
            ok, msg, oid = DB.appoint_official(
                id_e.get().strip(), resident_info['name'], resident_info.get('gender', ''),
                pos_var.get(), dept_var.get(), rank_var.get(), phone_e.get().strip(),
                resident_info.get('address', ''), ts, te,
                self.user['type'], self.user['id'], self.user['name'])
            if not ok:
                messagebox.showwarning('提示', msg)
                return
            messagebox.showinfo('成功', f'官员 {resident_info["name"]} 任命成功！\n'
                                        f'职务：{pos_var.get()}\n部门：{dept_var.get()}\n'
                                        f'级别：{rank_var.get()}\n任期：{ts} 至 {te}\n'
                                        f'（已计入官员档案，其首次登录须签署任职协议）')
            id_e.delete(0, 'end')
            name_e.delete(0, 'end')
            phone_e.delete(0, 'end')
            verify_result.set('')
            resident_info.clear()
            ts_e.delete(0, 'end')
            ts_e.insert(0, _today)
            te_e.delete(0, 'end')
            te_e.insert(0, _default_end)

        btn_frame = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        btn_frame.pack(pady=20)
        make_flat_button(btn_frame, "✅ 确认任命", add_official, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=18).pack()

    # ---------- 官员管理 ----------
    def _build_manage_officials(self, parent):
        search_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        search_f.pack(pady=5)
        tk.Label(search_f, text="搜索：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        search_e = make_groove_entry(search_f, width=25)
        search_e.pack(side='left', padx=5)
        status_var = tk.StringVar(value='全部')
        ttk.Combobox(search_f, textvariable=status_var, values=['全部', '在职', '已撤职', '已拉黑', '停职'],
                      font=("Arial", 11), width=10, state='readonly').pack(side='left', padx=10)

        list_frame = tk.LabelFrame(parent, text="官员列表", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '姓名', '身份证号', '部门', '职务', '级别', '状态', '任命日期')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '身份证号': 160, '任命日期': 140}, height=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load_data():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            kw = f'%{search_e.get().strip()}%'
            st = status_var.get()
            if st == '全部':
                rows = conn.execute("SELECT * FROM officials WHERE name LIKE ? OR id_number LIKE ? OR department LIKE ? "
                                    "ORDER BY CASE WHEN position='副部长' THEN 32 "
                                    "ELSE COALESCE((SELECT sort_order FROM positions WHERE name=officials.position),999) END, "
                                    "department, id",
                                    (kw, kw, kw)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM officials WHERE status=? AND (name LIKE ? OR id_number LIKE ?) "
                                    "ORDER BY CASE WHEN position='副部长' THEN 32 "
                                    "ELSE COALESCE((SELECT sort_order FROM positions WHERE name=officials.position),999) END, "
                                    "department, id",
                                    (st, kw, kw)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['name'], r['id_number'], r['department'],
                                                r['position'], r['rank'], r['status'], r['appointed_date']))

        load_data()
        tk.Button(search_f, text="搜索", command=load_data, width=8, bg=COLOR_PRIMARY, fg='white',
                  relief=tk.FLAT, cursor='hand2').pack(side='left', padx=5)

        def get_selected():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一位官员')
                return None
            return tree.item(sel[0])['values']

        def update_status(new_status, action_name):
            vals = get_selected()
            if not vals:
                return
            oid, name = vals[0], vals[1]
            ok, msg = DB.set_official_status(oid, new_status, action_name,
                                             self.user['type'], self.user['id'], self.user['name'])
            messagebox.showinfo('成功' if ok else '提示', msg)
            load_data()

        def promote():
            vals = get_selected()
            if not vals:
                return
            oid, name, rank = vals[0], vals[1], vals[5]
            idx = RANKS.index(rank) if rank in RANKS else 0
            if idx >= len(RANKS) - 1:
                messagebox.showinfo('提示', f'{name} 已达最高级别')
                return
            new_rank = RANKS[idx + 1]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM officials WHERE id=?", (oid,)).fetchone()
            conn.execute("UPDATE officials SET rank=?, last_update=? WHERE id=?",
                         (new_rank, now_str(), oid))
            if r:
                DB._add_career(conn, oid, '任职', r['position'], r['department'],
                               r['term_start'], r['term_end'],
                               f'级别晋升：{rank} → {new_rank}')
            conn.commit()
            conn.close()
            DB.log(self.user['type'], self.user['id'], self.user['name'], '升官', f'官员{name}从{rank}晋升为{new_rank}')
            messagebox.showinfo('成功', f'官员 {name} 晋升成功！\n{rank} → {new_rank}')
            load_data()

        def demote():
            vals = get_selected()
            if not vals:
                return
            oid, name, rank = vals[0], vals[1], vals[5]
            idx = RANKS.index(rank) if rank in RANKS else 0
            if idx <= 0:
                messagebox.showinfo('提示', f'{name} 已达最低级别')
                return
            new_rank = RANKS[idx - 1]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM officials WHERE id=?", (oid,)).fetchone()
            conn.execute("UPDATE officials SET rank=?, last_update=? WHERE id=?",
                         (new_rank, now_str(), oid))
            if r:
                DB._add_career(conn, oid, '任职', r['position'], r['department'],
                               r['term_start'], r['term_end'],
                               f'级别降级：{rank} → {new_rank}')
            conn.commit()
            conn.close()
            DB.log(self.user['type'], self.user['id'], self.user['name'], '降级', f'官员{name}从{rank}降为{new_rank}')
            messagebox.showinfo('成功', f'官员 {name} 降级处理！\n{rank} → {new_rank}')
            load_data()

        def change_position():
            vals = get_selected()
            if not vals:
                return
            oid, name = vals[0], vals[1]
            conn = DB.get_conn(GOV_DB)
            _cur = conn.execute("SELECT * FROM officials WHERE id=?", (oid,)).fetchone()
            conn.close()
            cur_ts = (_cur['term_start'] if _cur and _cur['term_start'] else datetime.now().strftime('%Y-%m-%d'))
            cur_te = (_cur['term_end'] if _cur and _cur['term_end']
                      else (datetime.now() + timedelta(days=365 * 2)).strftime('%Y-%m-%d'))
            win = tk.Toplevel(self.root)
            win.title(f'调整职务 - {name}')
            win.geometry('420x330')
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text=f'调整官员：{name}', font=("Arial", 12, "bold")).pack(pady=10)
            tk.Label(win, text='新部门：').pack()
            _dept_opts = DB.active_departments()
            if vals[3] and vals[3] not in _dept_opts:
                _dept_opts = [vals[3]] + _dept_opts
            dv = tk.StringVar(value=vals[3] if vals[3] else (_dept_opts[0] if _dept_opts else ''))
            dcb = ttk.Combobox(win, textvariable=dv, values=_dept_opts, state='readonly')
            dcb.pack(pady=5)
            tk.Label(win, text='新职务（随部门联动）：').pack()
            _pos_opts = DB.active_positions()
            pv = tk.StringVar(value=vals[4] if vals[4] in _pos_opts else (_pos_opts[-1] if _pos_opts else ''))
            pcb = ttk.Combobox(win, textvariable=pv, values=_pos_opts, state='readonly')
            pcb.pack(pady=5)

            tf = tk.Frame(win)
            tf.pack(pady=8)
            tk.Label(tf, text='起任日期：').grid(row=0, column=0, padx=5)
            ts_e = make_groove_entry(tf, width=13)
            ts_e.insert(0, cur_ts)
            ts_e.grid(row=0, column=1, padx=5)
            tk.Label(tf, text='卸任日期：').grid(row=0, column=2, padx=5)
            te_e = make_groove_entry(tf, width=13)
            te_e.insert(0, cur_te)
            te_e.grid(row=0, column=3, padx=5)
            tk.Label(win, text='任期届满自动卸任；调整后其下次登录须重新签署任职协议',
                     font=("Arial", 9), fg=COLOR_TEXT_GRAY).pack()

            def refresh_positions(_=None):
                opts = DB.positions_for_dept(dv.get()) if dv.get() else DB.active_positions()
                if vals[4] and vals[4] in opts:
                    pv.set(vals[4])
                elif pv.get() not in opts:
                    pv.set(opts[-1] if opts else '')
                pcb.configure(values=opts)
            dcb.bind('<<ComboboxSelected>>', refresh_positions)
            refresh_positions()

            def confirm():
                if not pv.get():
                    messagebox.showwarning('提示', '请选择职务'); return
                if not DB.validate_position_dept(pv.get(), dv.get()):
                    messagebox.showwarning('职务与部门不匹配',
                                           f'职务“{pv.get()}”不允许在“{dv.get()}”任职\n'
                                           f'（可先在【职务管理】设置该职务的所属部门，或在【部门管理】绑定职位）')
                    return
                # 职务唯一名额：总统/副总统/各部部长全国唯一；处长每部门最多一名（排除本人）
                occ, holder = DB.position_slot_occupied(pv.get(), dv.get(), oid)
                if occ:
                    messagebox.showwarning('职务名额已满',
                                           f'职务“{pv.get()}”已有在职担任者：{holder}\n'
                                           f'（总统、副总统、各部部长、处长同一时间只能一人担任）')
                    return
                ts, te = ts_e.get().strip(), te_e.get().strip()
                try:
                    datetime.strptime(ts, '%Y-%m-%d')
                    datetime.strptime(te, '%Y-%m-%d')
                except Exception:
                    messagebox.showwarning('提示', '请按 YYYY-MM-DD 格式填写起任/卸任日期')
                    return
                if te < ts:
                    messagebox.showwarning('提示', '卸任日期不能早于起任日期')
                    return
                ok, msg = DB.adjust_official(oid, pv.get(), dv.get(), vals[5] if len(vals) > 5 else '',
                                             ts, te, self.user['type'], self.user['id'], self.user['name'])
                messagebox.showinfo('成功' if ok else '提示', msg)
                win.destroy()
                load_data()
            make_flat_button(win, '确认调整', confirm, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12).pack(pady=15)

        def delete_official():
            vals = get_selected()
            if not vals:
                return
            oid, name = vals[0], vals[1]
            if not messagebox.askyesno('确认删除', f'确定要删除官员 {name} 吗？此操作不可恢复！'):
                return
            ok, msg = DB.delete_official_by_id(oid, self.user['id'], self.user['name'])
            messagebox.showinfo('成功' if ok else '提示', msg)
            load_data()

        def view_detail():
            vals = get_selected()
            if not vals:
                return
            oid = vals[0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM officials WHERE id=?", (oid,)).fetchone()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'官员详情 - {r["name"]}')
            win.geometry('500x450')
            win.transient(self.root)
            win.grab_set()
            info = [('姓名', r['name']), ('身份证号', r['id_number']), ('性别', r['gender']),
                    ('部门', r['department']), ('职务', r['position']), ('级别', r['rank']),
                    ('状态', r['status']), ('任命日期', r['appointed_date']),
                    ('联系电话', r['phone']), ('住址', r['address']), ('最后更新', r['last_update'])]
            for i, (k, v) in enumerate(info):
                tk.Label(win, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e').grid(row=i, column=0, sticky='e', padx=10, pady=6)
                tk.Label(win, text=str(v or '无'), font=("Arial", 10), anchor='w', wraplength=300).grid(row=i, column=1, sticky='w', pady=6)

        def export_excel():
            if not HAS_PANDAS:
                messagebox.showerror('错误', '需要 pandas 库才能导出 Excel，请先安装 pandas')
                return
            conn = DB.get_conn(GOV_DB)
            kw = f'%{search_e.get().strip()}%'
            st = status_var.get()
            if st == '全部':
                rows = conn.execute("SELECT * FROM officials WHERE name LIKE ? OR id_number LIKE ? OR department LIKE ? "
                                    "ORDER BY CASE WHEN position='副部长' THEN 32 "
                                    "ELSE COALESCE((SELECT sort_order FROM positions WHERE name=officials.position),999) END, "
                                    "department, id",
                                    (kw, kw, kw)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM officials WHERE status=? AND (name LIKE ? OR id_number LIKE ?) "
                                    "ORDER BY CASE WHEN position='副部长' THEN 32 "
                                    "ELSE COALESCE((SELECT sort_order FROM positions WHERE name=officials.position),999) END, "
                                    "department, id",
                                    (st, kw, kw)).fetchall()
            conn.close()
            if not rows:
                messagebox.showwarning('提示', '当前筛选条件下没有可导出的官员数据')
                return
            path = filedialog.asksaveasfilename(
                defaultextension='.xlsx',
                initialfile=f"官员信息导出_{datetime.now():%Y%m%d_%H%M%S}.xlsx",
                filetypes=[('Excel', '*.xlsx')])
            if not path:
                return
            pd.DataFrame([(r['id'], r['name'], r['id_number'], r['gender'], r['department'],
                           r['position'], r['rank'], r['status'], r['appointed_date'],
                           r['phone'], r['address'], r['last_update']) for r in rows],
                         columns=['ID', '姓名', '身份证号', '性别', '部门', '职务', '级别', '状态',
                                  '任命日期', '联系电话', '住址', '最后更新']).to_excel(path, index=False)
            DB.log(self.user['type'], self.user['id'], self.user['name'], '导出官员信息',
                   f'导出官员信息 {len(rows)} 条（状态筛选：{st}，关键词：{search_e.get().strip() or "全部"}）')
            messagebox.showinfo('成功', f'已导出 {len(rows)} 条官员信息：\n{path}')

        def restore_active():
            vals = get_selected()
            if not vals:
                return
            if vals[6] == '在职':
                messagebox.showinfo('提示', f'{vals[1]} 已在职，无需恢复')
                return
            update_status('在职', '恢复在职')

        op_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        op_f.pack(pady=10)
        buttons = [
            ('👁️ 查看详情', view_detail, COLOR_PRIMARY, COLOR_PRIMARY_HOVER),
            ('📤 导出Excel', export_excel, COLOR_SUCCESS, COLOR_SUCCESS_HOVER),
            ('✅ 恢复在职', restore_active, COLOR_SUCCESS, COLOR_SUCCESS_HOVER),
            ('⬆️ 升官', promote, COLOR_SUCCESS, COLOR_SUCCESS_HOVER),
            ('⬇️ 降级', demote, COLOR_WARNING, COLOR_WARNING),
            ('🔄 调整职务', change_position, COLOR_PURPLE, COLOR_PURPLE),
            ('🚫 撤职', lambda: update_status('已撤职', '撤职'), '#e67e22', '#d35400'),
            ('⛔ 拉黑', lambda: update_status('已拉黑', '拉黑'), COLOR_DANGER, COLOR_DANGER_HOVER),
            ('❌ 删除', delete_official, '#c0392b', '#a93226'),
        ]
        for text, cmd, color, hover in buttons:
            make_flat_button(op_f, text, cmd, color, hover, width=10, height=1, font_size=10).pack(side='left', padx=5)

    # ========== 文件审批（通用，修复按部门路由） ==========
    def _build_approval_list(self, parent, can_approve=False):
        filter_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        filter_f.pack(pady=5)
        tk.Label(filter_f, text="状态：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        st_var = tk.StringVar(value='待审批')
        ttk.Combobox(filter_f, textvariable=st_var, values=['全部', '待审批', '已通过', '已驳回'],
                      font=("Arial", 11), width=10, state='readonly').pack(side='left', padx=5)

        list_frame = tk.LabelFrame(parent, text="审批文件列表", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '标题', '类型', '申请人', '申请类型', '承办部门', '状态', '审批人', '申请时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '标题': 180, '承办部门': 90, '申请时间': 130}, height=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            st = st_var.get()
            # 官员：仅显示其“审批范围”内的单据；管理员看全部
            if self.user['type'] == 'official':
                allowed = DB.scope_department_set(self._pinfo())
                if st == '全部':
                    allrows = conn.execute(
                        "SELECT * FROM approval_files ORDER BY id DESC").fetchall()
                else:
                    allrows = conn.execute(
                        "SELECT * FROM approval_files WHERE status=? ORDER BY id DESC",
                        (st,)).fetchall()
                if allowed is None:
                    rows = list(allrows)
                else:
                    rows = [x for x in allrows
                            if x['target_department'] and x['target_department'] in allowed]
            else:
                if st == '全部':
                    rows = conn.execute("SELECT * FROM approval_files ORDER BY id DESC").fetchall()
                else:
                    rows = conn.execute("SELECT * FROM approval_files WHERE status=? ORDER BY id DESC", (st,)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], r['file_type'], r['applicant_name'],
                                                r['applicant_type'], r['target_department'] or '未指定',
                                                r['status'], r['approver_name'] or '未审批', r['created_at']))

        load()
        st_var.trace_add('write', lambda *a: load())

        def view_file():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一个文件')
                return
            fid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM approval_files WHERE id=?", (fid,)).fetchone()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'审批文件详情 - {r["title"]}')
            win.geometry('650x550')
            win.transient(self.root)
            win.grab_set()
            info_f = tk.Frame(win, bg='white')
            info_f.pack(fill='x', padx=15, pady=10)
            info = [('文件标题', r['title']), ('文件类型', r['file_type']), ('申请人', r['applicant_name']),
                    ('申请人类型', r['applicant_type']), ('承办部门', r['target_department'] or '未指定'),
                    ('状态', r['status']), ('审批人', r['approver_name'] or '未审批'),
                    ('申请时间', r['created_at']), ('审批时间', r['approved_at'] or '未审批')]
            for i, (k, v) in enumerate(info):
                tk.Label(info_f, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e', bg='white').grid(
                    row=i // 3, column=(i % 3) * 2, sticky='e', padx=5, pady=3)
                tk.Label(info_f, text=str(v or '-'), font=("Arial", 10), anchor='w', bg='white').grid(
                    row=i // 3, column=(i % 3) * 2 + 1, sticky='w', pady=3)
            tk.Label(win, text='文件内容：', font=("Arial", 11, "bold")).pack(anchor='w', padx=15)
            txt = scrolledtext.ScrolledText(win, font=("Arial", 11), height=9, wrap='word')
            txt.pack(fill='both', expand=True, padx=15, pady=5)
            txt.insert('1.0', r['content'])
            txt.config(state='disabled')
            if r['approve_comment']:
                tk.Label(win, text='审批意见：', font=("Arial", 11, "bold")).pack(anchor='w', padx=15)
                ct = scrolledtext.ScrolledText(win, font=("Arial", 11), height=3, wrap='word', bg='#fff9e6')
                ct.pack(fill='x', padx=15, pady=5)
                ct.insert('1.0', r['approve_comment'])
                ct.config(state='disabled')
            if can_approve and r['status'] == '待审批':
                op_f = tk.Frame(win)
                op_f.pack(fill='x', padx=15, pady=10)
                tk.Label(op_f, text='审批意见：', font=("Arial", 11)).pack(side='left')
                comment_e = make_groove_entry(op_f, width=35)
                comment_e.pack(side='left', padx=5)
                def do_approve(result):
                    comment = comment_e.get().strip() or ('同意' if result == '已通过' else '驳回')
                    # 二次校验审批范围，防止越权审批
                    if self.user['type'] == 'official' and \
                            not DB.can_approve_department(self.user, r['target_department']):
                        messagebox.showerror('无权审批', '该文件承办部门不在您的审批范围内，操作已被拦截。')
                        return
                    conn = DB.get_conn(GOV_DB)
                    conn.execute('''UPDATE approval_files SET status=?, approver_id=?, approver_name=?,
                        approve_comment=?, approved_at=? WHERE id=?''',
                        (result, str(self.user['id']), self.user['name'], comment,
                         now_str(), fid))
                    conn.commit()
                    conn.close()
                    DB.log(self.user['type'], self.user['id'], self.user['name'], f'审批文件{result}',
                           f'文件《{r["title"]}》审批结果：{result}')
                    # 通知申请人
                    DB.send_message('citizen', r['applicant_id'], '审批结果通知',
                                    f'您提交的文件《{r["title"]}》审批结果：{result}。审批意见：{comment}')
                    messagebox.showinfo('成功', f'文件已{result}')
                    win.destroy()
                    load()
                make_flat_button(op_f, '✅ 通过', lambda: do_approve('已通过'), COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=8, height=1).pack(side='right', padx=5)
                make_flat_button(op_f, '❌ 驳回', lambda: do_approve('已驳回'), COLOR_DANGER, COLOR_DANGER_HOVER, width=8, height=1).pack(side='right', padx=5)

        btn_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '📄 查看详情', view_file, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12, height=1).pack(side='left', padx=5)

    def _show_approval_official(self):
        if not self._guard('approve', '审批决定'):
            return
        self._clear_main()
        scope = self._pinfo()['scope']
        make_page_title(self.main_frame, f"📋 待办审批（{SCOPE_LABELS.get(scope, scope)}）").pack(pady=20)
        self._build_approval_list(self.main_frame, can_approve=True)

    # ========== 官员：离职申请（提交给上级审核） ==========
    def _show_resign_apply(self):
        self._clear_main()
        make_page_title(self.main_frame, "📋 离职申请（提交后由上级审核）").pack(pady=12)
        form = tk.LabelFrame(self.main_frame, text="填写离职申请", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=15, pady=10)
        form.pack(fill='x', padx=30, pady=6)
        tk.Label(form, text="离职原因：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w')
        reason_t = scrolledtext.ScrolledText(form, font=("Arial", 10), width=80, height=5, wrap='word')
        reason_t.pack(fill='x', pady=4)

        def do_submit():
            reason = reason_t.get('1.0', 'end').strip()
            ok, msg, _rid = DB.submit_resignation(self.user, reason)
            messagebox.showinfo('成功' if ok else '提示', msg)
            if ok:
                reason_t.delete('1.0', 'end')
                load_mine()

        make_flat_button(form, '📤 提交离职申请', do_submit, COLOR_DANGER, COLOR_DANGER, width=18).pack(pady=6)

        mine = tk.LabelFrame(self.main_frame, text="我的离职申请记录", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=10, pady=8)
        mine.pack(fill='both', expand=True, padx=30, pady=6)
        cols = ('id', '申请时间', '原因', '状态', '审核人', '审核备注', '审核时间')
        tf, tree = make_tree(mine, cols, {'id': 40, '申请时间': 130, '原因': 280, '状态': 80,
                                          '审核人': 90, '审核备注': 180, '审核时间': 130}, height=8)
        tf.pack(fill='both', expand=True)

        def load_mine():
            for it in tree.get_children():
                tree.delete(it)
            for r in DB.my_resignations(self.user['id']):
                tree.insert('', 'end', values=(r['id'], r['created_at'][:16], r['reason'],
                                               r['status'], r['reviewer_name'] or '-',
                                               r['review_note'] or '-', (r['reviewed_at'] or '')[:16]))
        load_mine()

    # ========== 官员：离职审批（上级审核） ==========
    def _show_resign_review(self):
        self._clear_main()
        make_page_title(self.main_frame, "📋 离职审批（下属离职申请待您审核）").pack(pady=12)
        wrap = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        wrap.pack(fill='both', expand=True, padx=30, pady=6)
        cols = ('id', '申请人', '部门', '职务', '离职原因', '提交时间')
        tf, tree = make_tree(wrap, cols, {'id': 40, '申请人': 80, '部门': 130, '职务': 110,
                                          '离职原因': 280, '提交时间': 130}, height=9)
        tf.pack(fill='both', expand=True)

        sel_id = {'v': None}

        def load():
            for it in tree.get_children():
                tree.delete(it)
            for r in DB.pending_resignations(self.user):
                tree.insert('', 'end', values=(r['id'], r['official_name'], r['department'],
                                               r['position'], r['reason'], r['created_at'][:16]))
            if not tree.get_children():
                tk.Label(wrap, text='当前没有待您审核的离职申请', font=("Arial", 11),
                         bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=12)
        load()
        tree.bind('<<TreeviewSelect>>',
                  lambda e: sel_id.__setitem__('v', int(tree.item(tree.selection()[0])['values'][0])
                                               if tree.selection() else None))

        def ask_note(default_txt):
            win = tk.Toplevel(self.root)
            win.title('审核备注')
            win.geometry('420x200')
            win.transient(self.root); win.grab_set()
            win.configure(bg='white')
            tk.Label(win, text=default_txt, font=("Arial", 12, "bold"), bg='white').pack(pady=8)
            note_t = scrolledtext.ScrolledText(win, font=("Arial", 10), width=46, height=5, wrap='word')
            note_t.pack(padx=10)
            result = {'v': ''}

            def ok():
                result['v'] = note_t.get('1.0', 'end').strip()
                win.destroy()
            make_flat_button(win, '确定', ok, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10).pack(pady=6)
            win.wait_window()
            return result['v']

        def do_review(approve):
            if not sel_id['v']:
                messagebox.showwarning('提示', '请先选择一条离职申请'); return
            note = ask_note('审核通过，确认办理离职？' if approve else '填写驳回理由：') if not approve else ''
            if not approve and not note:
                messagebox.showwarning('提示', '请填写驳回理由'); return
            ok, msg = DB.review_resignation(sel_id['v'], dict(self.user), approve, note)
            messagebox.showinfo('成功' if ok else '提示', msg)
            if ok:
                load()

        bf = tk.Frame(wrap, bg=COLOR_CONTENT_BG); bf.pack(pady=8)
        make_flat_button(bf, '✅ 同意离职', lambda: do_review(True), COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12).pack(side='left', padx=6)
        make_flat_button(bf, '❌ 驳回申请', lambda: do_review(False), COLOR_DANGER, COLOR_DANGER, width=12).pack(side='left', padx=6)
        make_flat_button(bf, '🔄 刷新', load, COLOR_GRAY, COLOR_GRAY, width=8).pack(side='left', padx=6)

    # ========== 官员：会议投票中心 ==========
    def _show_meeting_center(self):
        self._clear_main()
        make_page_title(self.main_frame, "🗳️ 会议投票").pack(pady=15)
        can_conv = DB.can_convene_meeting(self.user)
        tip = '您可召开会议并组织投票' if can_conv else '仅总统与各部部长可召开会议；您可参加被邀请的会议并投票'
        top_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG); top_f.pack(fill='x', padx=25)
        tk.Label(top_f, text=tip, font=("Arial", 10), fg=COLOR_TEXT_GRAY,
                 bg=COLOR_CONTENT_BG).pack(side='left')
        if can_conv:
            make_flat_button(top_f, '➕ 召开会议', self._open_create_meeting,
                             COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12).pack(side='right')

        nb = ttk.Notebook(self.main_frame)
        nb.pack(fill='both', expand=True, padx=25, pady=12)
        tab_join = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        tab_mine = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        nb.add(tab_join, text='  📥 我参加的会议  ')
        if can_conv:
            nb.add(tab_mine, text='  🎤 我召开的会议  ')

        cols = ('id', '议题', '召开人', '关联审批文件', '状态', '赞成', '反对', '弃权', '应到', '召开时间')
        widths = {'id': 40, '议题': 220, '召开人': 80, '关联审批文件': 170, '状态': 70,
                  '赞成': 50, '反对': 50, '弃权': 50, '应到': 50, '召开时间': 140}
        fj, tree_join = make_tree(tab_join, cols, widths, height=14)
        fj.pack(fill='both', expand=True, padx=8, pady=8)
        fm, tree_mine = make_tree(tab_mine, cols, widths, height=14)
        fm.pack(fill='both', expand=True, padx=8, pady=8)

        def row_values(m):
            return (m['id'], m['title'], m['convener_name'], m['approval_title'] or '（无）',
                    m['status'], m['yes_count'], m['no_count'], m['abstain_count'],
                    m['total_count'], m['created_at'])

        def load():
            for t in (tree_join, tree_mine):
                for it in t.get_children():
                    t.delete(it)
            for m in DB.meetings_for_official(self.user['id'], as_convener=False):
                tree_join.insert('', 'end', values=row_values(m))
            for m in DB.meetings_for_official(self.user['id'], as_convener=True):
                tree_mine.insert('', 'end', values=row_values(m))

        def open_by_tree(tree, as_convener=False):
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一场会议'); return
            mid = int(tree.item(sel[0])['values'][0])
            self._open_meeting_detail(mid, load)

        bj = tk.Frame(tab_join, bg=COLOR_CONTENT_BG); bj.pack(pady=4)
        make_flat_button(bj, '📂 进入会议', lambda: open_by_tree(tree_join),
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12).pack(side='left', padx=6)
        if can_conv:
            bm = tk.Frame(tab_mine, bg=COLOR_CONTENT_BG); bm.pack(pady=4)
            make_flat_button(bm, '📊 查看结果/审批', lambda: open_by_tree(tree_mine, True),
                             COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14).pack(side='left', padx=6)
        load()

    # ---- 召开会议 ----
    def _open_create_meeting(self):
        if not DB.can_convene_meeting(self.user):
            messagebox.showwarning('无权限', '只有总统（总统府）与各部部长可以召开会议'); return
        win = tk.Toplevel(self.root)
        win.title('召开会议')
        win.geometry('760x640')
        win.transient(self.root); win.grab_set()
        win.configure(bg='white')
        tk.Label(win, text='🎤 召开会议并发起投票', font=("Arial", 16, 'bold'),
                 bg='white', fg=COLOR_TITLE).pack(pady=10)

        form = tk.Frame(win, bg='white'); form.pack(fill='x', padx=20)
        tk.Label(form, text='会议议题：', font=("Arial", 11, 'bold'), bg='white').grid(
            row=0, column=0, sticky='e', pady=6, padx=4)
        title_var = tk.StringVar()
        tk.Entry(form, textvariable=title_var, font=("Arial", 11), width=52).grid(
            row=0, column=1, columnspan=3, sticky='w', pady=6)
        tk.Label(form, text='会议说明：', font=("Arial", 11, 'bold'), bg='white').grid(
            row=1, column=0, sticky='ne', pady=6, padx=4)
        content_t = scrolledtext.ScrolledText(form, font=("Arial", 10), width=52, height=4, wrap='word')
        content_t.grid(row=1, column=1, columnspan=3, sticky='w', pady=6)

        # 关联待审批文件（可选）
        tk.Label(form, text='关联审批文件：', font=("Arial", 11, 'bold'), bg='white').grid(
            row=2, column=0, sticky='e', pady=6, padx=4)
        pendings = DB.pending_files_for_convener(self.user)
        file_opts = ['（不关联，仅普通投票）']
        file_map = {}
        for r in pendings:
            label = f"[{r['id']}] {r['title']}（送：{r['target_department'] or '未指定'}）"
            file_opts.append(label); file_map[label] = r['id']
        file_var = tk.StringVar(value=file_opts[0])
        ttk.Combobox(form, textvariable=file_var, values=file_opts, font=("Arial", 10),
                     width=50, state='readonly').grid(row=2, column=1, columnspan=3, sticky='w', pady=6)
        if not pendings:
            tk.Label(form, text='（当前没有需要您审批的待办文件，可仅发起普通投票）',
                     font=("Arial", 9), fg=COLOR_TEXT_GRAY, bg='white').grid(
                row=3, column=1, columnspan=3, sticky='w')

        # 参会人员选择
        pf = tk.LabelFrame(win, text='选择参会人员（在职官员，可搜索；召开人自动包含）',
                           font=("Arial", 11, 'bold'), bg='white', padx=8, pady=6)
        pf.pack(fill='both', expand=True, padx=20, pady=8)
        sf = tk.Frame(pf, bg='white'); sf.pack(fill='x')
        tk.Label(sf, text='搜索：', font=("Arial", 10), bg='white').pack(side='left')
        kw_var = tk.StringVar()
        tk.Entry(sf, textvariable=kw_var, font=("Arial", 10), width=20).pack(side='left', padx=4)
        sel_all_var = tk.IntVar(value=1)
        officials = DB.active_officials()
        check_vars = {}
        rows_widget = []
        canvas = tk.Canvas(pf, height=210, highlightthickness=0, bg='white')
        sb = ttk.Scrollbar(pf, orient='vertical', command=canvas.yview)
        inner = tk.Frame(canvas, bg='white')
        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        def build_checks():
            for w in inner.winfo_children():
                w.destroy()
            check_vars.clear(); rows_widget.clear()
            kw = kw_var.get().strip()
            shown = 0
            for o in officials:
                label = f"{o['name']}　{o['department']}·{o['position']}（{o['rank']}）"
                if kw and kw not in o['name'] and kw not in (o['department'] or '') \
                        and kw not in (o['position'] or ''):
                    continue
                var = check_vars.get(o['id'])
                if var is None:
                    var = tk.IntVar(value=1 if sel_all_var.get() else 0)
                    check_vars[o['id']] = var
                b = tk.Checkbutton(inner, text=label, variable=var, bg='white', font=("Arial", 10),
                                   anchor='w')
                b.grid(row=shown, column=0, sticky='w', padx=4, pady=1)
                rows_widget.append(b); shown += 1
            if shown == 0:
                tk.Label(inner, text='无匹配人员', bg='white', fg=COLOR_TEXT_GRAY).grid(
                    row=0, column=0, padx=4)

        def toggle_all():
            val = sel_all_var.get()
            for o in officials:
                check_vars.setdefault(o['id'], tk.IntVar()).set(val)

        tk.Checkbutton(sf, text='全选/全不选', variable=sel_all_var, bg='white',
                       command=toggle_all, font=("Arial", 10)).pack(side='left', padx=10)
        tk.Button(sf, text='筛选', command=build_checks, font=("Arial", 9)).pack(side='left')
        kw_var.trace_add('write', lambda *a: build_checks())
        build_checks()

        def submit():
            pids = [pid for pid, v in check_vars.items() if v.get()]
            approval_id = file_map.get(file_var.get())
            ok, msg, mid = DB.create_meeting(
                title_var.get(), content_t.get('1.0', 'end').strip(), self.user, approval_id, pids)
            if not ok:
                messagebox.showwarning('无法创建', msg); return
            messagebox.showinfo('成功', f'{msg}\n会议编号：{mid}')
            win.destroy()
        bf = tk.Frame(win, bg='white'); bf.pack(pady=10)
        make_flat_button(bf, '✅ 创建会议并开始投票', submit, COLOR_SUCCESS,
                         COLOR_SUCCESS_HOVER, width=20).pack(side='left', padx=8)
        make_flat_button(bf, '取消', win.destroy, COLOR_GRAY, COLOR_TEXT_GRAY, width=8).pack(side='left')

    # ---- 会议详情（投票 / 看结果 / 审批 同一窗口，按身份与状态切换） ----
    def _open_meeting_detail(self, mid, on_close=None):
        m = DB.get_meeting(mid)
        if not m:
            messagebox.showerror('错误', '会议不存在'); return
        is_conv = (m['convener_id'] == self.user['id'])
        if not is_conv and not DB.is_participant(mid, self.user['id']):
            messagebox.showwarning('无权查看', '您不是本次会议的参会人员'); return

        win = tk.Toplevel(self.root)
        win.title(f"会议 - {m['title']}")
        win.geometry('720x680')
        win.transient(self.root); win.grab_set()
        win.configure(bg='white')

        head = tk.Frame(win, bg='white'); head.pack(fill='x', padx=20, pady=10)
        tk.Label(head, text=f"🗳️ {m['title']}", font=("Arial", 17, 'bold'),
                 bg='white', fg=COLOR_TITLE).pack(anchor='w')
        meta = (f"召开人：{m['convener_name']}　|　状态：{m['status']}　|　召开时间：{m['created_at']}")
        tk.Label(head, text=meta, font=("Arial", 10), fg=COLOR_TEXT_GRAY, bg='white').pack(anchor='w', pady=2)
        if m['approval_title']:
            tk.Label(head, text=f"关联待审批文件：[{m['approval_id']}] {m['approval_title']}",
                     font=("Arial", 10, 'bold'), fg=COLOR_PURPLE, bg='white').pack(anchor='w')
        if m['content']:
            cf = tk.LabelFrame(win, text='会议说明', font=("Arial", 10, 'bold'), bg='white')
            cf.pack(fill='x', padx=20)
            ct = scrolledtext.ScrolledText(cf, font=("Arial", 10), height=3, wrap='word')
            ct.pack(fill='x', padx=6, pady=4); ct.insert('1.0', m['content']); ct.config(state='disabled')

        def render():
            nonlocal m
            m = DB.get_meeting(mid)
            if not m:
                messagebox.showerror('错误', '会议不存在'); win.destroy(); return None
            body = tk.Frame(win, bg='white'); body.pack(fill='both', expand=True, padx=20, pady=8)
            st = DB.meeting_vote_stats(mid)
            # 统计区
            sf = tk.LabelFrame(body, text='实时投票结果', font=("Arial", 12, 'bold'), bg='white',
                               padx=10, pady=10)
            sf.pack(fill='x')
            total = max(st['total'], 1)
            cards = [('赞成', st['yes'], COLOR_SUCCESS), ('反对', st['no'], COLOR_DANGER),
                     ('弃权', st['abstain'], COLOR_GRAY),
                     ('未投票', st['unvoted'], COLOR_WARNING)]
            cw = tk.Frame(sf, bg='white'); cw.pack(fill='x')
            for i, (lab, num, color) in enumerate(cards):
                card = tk.Frame(cw, bg=color, width=140, height=64)
                card.pack(side='left', padx=8, pady=4)
                card.pack_propagate(False)
                tk.Label(card, text=str(num), font=("Arial", 22, 'bold'), fg='white',
                         bg=color).pack(pady=(6, 0))
                tk.Label(card, text=f"{lab}（{num * 100 // total}%）", font=("Arial", 9),
                         fg='white', bg=color).pack()
            tk.Label(sf, text=f"已投 {st['voted']} / 应到 {st['total']} 人",
                     font=("Arial", 10), bg='white', fg=COLOR_TEXT_GRAY).pack(anchor='w', pady=(6, 0))

            # 结论区（已审批）
            if m['status'] == '已审批':
                df = tk.LabelFrame(body, text='召开人最终审批', font=("Arial", 11, 'bold'),
                                   bg='#f4f8ff')
                df.pack(fill='x', pady=8)
                tk.Label(df, text=f"结论：{m['final_decision']}　|　时间：{m['decided_at']}",
                         font=("Arial", 11, 'bold'), fg=COLOR_TITLE, bg='#f4f8ff').pack(anchor='w', padx=8, pady=4)
                if m['decision_comment']:
                    tk.Label(df, text=f"意见：{m['decision_comment']}", font=("Arial", 10),
                             bg='#f4f8ff', wraplength=620, justify='left').pack(anchor='w', padx=8)
                if is_conv:
                    make_flat_button(df, '🔓 重新开放投票', lambda: [DB.reopen_meeting(mid),
                                     messagebox.showinfo('提示', '投票已重新开放'), win.destroy()],
                                     COLOR_WARNING, COLOR_WARNING, width=14).pack(anchor='w', padx=8, pady=6)
                return body

            # 召开人操作区
            if is_conv:
                af = tk.LabelFrame(body, text='召开人操作', font=("Arial", 11, 'bold'), bg='white')
                af.pack(fill='x', pady=8)
                if m['status'] == '投票中':
                    make_flat_button(af, '🔒 结束投票', lambda: do_close(),
                                     COLOR_WARNING, COLOR_WARNING, width=12).pack(side='left', padx=8, pady=8)
                    tk.Label(af, text='结束投票后将不能再投票，随后可做出审批。',
                             font=("Arial", 9), fg=COLOR_TEXT_GRAY, bg='white').pack(side='left')
                else:  # 已结束，待审批
                    if m['approval_title']:
                        tk.Label(af, text='根据投票结果对关联文件做出最终审批：',
                                 font=("Arial", 10, 'bold'), bg='white').pack(anchor='w', padx=8, pady=(6, 0))
                    else:
                        tk.Label(af, text='记录本次会议的最终结论：',
                                 font=("Arial", 10, 'bold'), bg='white').pack(anchor='w', padx=8, pady=(6, 0))
                    cf2 = tk.Frame(af, bg='white'); cf2.pack(fill='x', padx=8, pady=4)
                    tk.Label(cf2, text='审批意见：', font=("Arial", 10), bg='white').pack(side='left')
                    dec_e = tk.Entry(cf2, font=("Arial", 10), width=44)
                    dec_e.pack(side='left', padx=4)
                    make_flat_button(af, '✅ 通过', lambda: do_decide('已通过', dec_e.get()),
                                     COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=10).pack(side='right', padx=8, pady=6)
                    make_flat_button(af, '❌ 驳回', lambda: do_decide('已驳回', dec_e.get()),
                                     COLOR_DANGER, COLOR_DANGER_HOVER, width=10).pack(side='right')
            else:
                # 普通参会人投票区
                vf = tk.LabelFrame(body, text='我的投票', font=("Arial", 11, 'bold'), bg='white')
                vf.pack(fill='x', pady=8)
                if m['status'] != '投票中':
                    tk.Label(vf, text='本次会议投票已结束。', font=("Arial", 11),
                             fg=COLOR_WARNING, bg='white').pack(pady=8)
                elif DB.has_voted(mid, self.user['id']):
                    conn = DB.get_conn(GOV_DB)
                    mine = conn.execute("SELECT option,comment FROM meeting_votes WHERE meeting_id=? AND official_id=?",
                                        (mid, self.user['id'])).fetchone()
                    conn.close()
                    tk.Label(vf, text=f"✅ 您已投票：{mine['option']}" +
                                  (f"（{mine['comment']}）" if mine['comment'] else ''),
                             font=("Arial", 11, 'bold'), fg=COLOR_SUCCESS, bg='white').pack(pady=8)
                else:
                    tk.Label(vf, text='请选择您的态度并可填写意见：', font=("Arial", 10),
                             bg='white').pack(anchor='w', padx=8, pady=(6, 0))
                    row = tk.Frame(vf, bg='white'); row.pack(fill='x', padx=8, pady=4)
                    cmt = tk.Entry(row, font=("Arial", 10), width=40)
                    cmt.pack(side='left', padx=(0, 8))
                    for opt, color, hover in [('赞成', COLOR_SUCCESS, COLOR_SUCCESS_HOVER),
                                              ('反对', COLOR_DANGER, COLOR_DANGER_HOVER),
                                              ('弃权', COLOR_GRAY, COLOR_TEXT_GRAY)]:
                        make_flat_button(row, opt, lambda o=opt: do_vote(o, cmt.get()),
                                         color, hover, width=7).pack(side='left', padx=4)

            # 召开人可见投票明细 + 未投名单
            if is_conv:
                df2 = tk.LabelFrame(body, text='投票明细（仅召开人可见）', font=("Arial", 11, 'bold'),
                                    bg='white')
                df2.pack(fill='both', expand=True, pady=6)
                cols = ('name', 'department', 'position', 'option', 'comment', 'time')
                tf2, tr2 = make_tree(df2, cols,
                                     {'name': 90, 'department': 100, 'position': 90, 'option': 60,
                                      'comment': 200, 'time': 140}, height=7)
                tf2.pack(fill='both', expand=True, padx=6, pady=6)
                for d in st['details']:
                    tr2.insert('', 'end', values=(d['voter_name'], d['department'] or '',
                                                   d['position'] or '', d['option'],
                                                   d['comment'] or '', d['created_at']))
            return body

        def refresh():
            if hasattr(win, '_body') and win._body.winfo_exists():
                win._body.destroy()
            win._body = render()

        def do_vote(opt, comment):
            ok, msg = DB.cast_vote(mid, self.user['id'], self.user['name'], opt, comment)
            if not ok:
                messagebox.showwarning('投票失败', msg); return
            messagebox.showinfo('成功', msg)
            refresh()

        def do_close():
            if not messagebox.askyesno('确认结束', '确定结束投票？结束后参会人将无法再投票。'):
                return
            DB.recalc_and_close(mid)
            DB.log('official', self.user['id'], self.user['name'], '结束会议投票', f'会议《{m["title"]}》')
            refresh()

        def do_decide(decision, comment):
            ok, msg = DB.decide_meeting(mid, self.user, decision, comment)
            if not ok:
                messagebox.showwarning('无法审批', msg); return
            messagebox.showinfo('成功', msg)
            win.destroy()
            if on_close:
                on_close()

        win._body = render()

    # ========== 官员：审批历史 ==========
    def _show_approval_history(self):
        self._clear_main()
        make_page_title(self.main_frame, "📜 审批历史").pack(pady=20)

        list_frame = tk.LabelFrame(self.main_frame, text="我审批过的文件", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '标题', '类型', '申请人', '状态', '审批意见', '审批时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '标题': 220, '审批意见': 200, '审批时间': 140}, height=15)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM approval_files WHERE approver_id=? ORDER BY id DESC", (str(self.user['id']),)).fetchall()
        conn.close()
        for r in rows:
            tree.insert('', 'end', values=(r['id'], r['title'], r['file_type'], r['applicant_name'],
                                            r['status'], r['approve_comment'] or '-', r['approved_at'] or '-'))

    # ========== 管理员：部门管理 ==========
    def _show_department_mgmt(self):
        self._clear_main()
        make_page_title(self.main_frame, "🏛️ 部门管理（部门权力与层级）").pack(pady=15)

        wrap = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        wrap.pack(fill='both', expand=True, padx=25, pady=5)

        # ---- 左：编辑表单 ----
        form = tk.LabelFrame(wrap, text="部门信息", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=15, pady=12)
        form.pack(side='left', fill='y', padx=(0, 15))
        sel_id = {'v': None}

        def field(row, text, w=22):
            tk.Label(form, text=text, font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
                row=row, column=0, sticky='e', pady=6, padx=5)
            e = make_groove_entry(form, width=w)
            e.grid(row=row, column=1, pady=6)
            return e

        name_e = field(0, "部门名称：")
        parent_var = tk.StringVar()
        tk.Label(form, text="上级部门：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=1, column=0, sticky='e', pady=6)
        parent_cb = ttk.Combobox(form, textvariable=parent_var, font=("Arial", 11),
                                 width=20, state='readonly')
        parent_cb.grid(row=1, column=1, pady=6)
        power_var = tk.StringVar(value='60')
        tk.Label(form, text="权力分值：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=2, column=0, sticky='e', pady=6)
        ttk.Spinbox(form, from_=0, to=999, textvariable=power_var, width=10,
                    font=("Arial", 11)).grid(row=2, column=1, sticky='w', pady=6)
        sort_var = tk.StringVar(value='0')
        tk.Label(form, text="排序：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=3, column=0, sticky='e', pady=6)
        ttk.Spinbox(form, from_=0, to=999, textvariable=sort_var, width=10,
                    font=("Arial", 11)).grid(row=3, column=1, sticky='w', pady=6)
        head_e = field(4, "负责人：")
        active_var = tk.IntVar(value=1)
        tk.Checkbutton(form, text="启用该部门", variable=active_var, bg=COLOR_CONTENT_BG,
                       font=("Arial", 11)).grid(row=5, column=1, sticky='w', pady=6)
        tk.Label(form, text="提示：权力分值越大、层级越高，\n该部门官员默认可管辖范围越大。",
                 font=("Arial", 9), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY, justify='left').grid(
            row=6, column=0, columnspan=2, pady=(8, 0))

        # ---- 右：部门列表 ----
        list_f = tk.LabelFrame(wrap, text="部门权力列表", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(side='left', fill='both', expand=True)
        cols = ('id', '部门名称', '上级部门', '权力分值', '负责人', '排序', '状态')
        tf, tree = make_tree(list_f, cols, {'id': 40, '部门名称': 130, '上级部门': 120,
                                            '权力分值': 80, '负责人': 90, '排序': 50, '状态': 70}, height=10)
        tf.pack(fill='both', expand=True)

        # ---- 部门职位绑定 ----
        pos_frame = tk.LabelFrame(list_f, text="本部门职位绑定（选中部门后：本部门可任职务 / 通用职位）",
                                  font=("Arial", 11, "bold"), bg=COLOR_CONTENT_BG, padx=10, pady=6)
        pos_frame.pack(fill='x', pady=(10, 0))
        cur_dept = {'v': ''}
        tk.Label(pos_frame, text="本部门可任职务：", font=("Arial", 10), bg=COLOR_CONTENT_BG).pack(anchor='w')
        bound_lb = tk.Listbox(pos_frame, height=4, font=("Arial", 9), exportselection=False)
        bound_lb.pack(fill='x', pady=2)
        row2 = tk.Frame(pos_frame, bg=COLOR_CONTENT_BG); row2.pack(fill='x', pady=4)
        tk.Label(row2, text="通用职位（可绑定到本部门）：", font=("Arial", 10), bg=COLOR_CONTENT_BG).pack(side='left')
        gen_var = tk.StringVar()
        gen_cb = ttk.Combobox(row2, textvariable=gen_var, state='readonly', width=24, font=("Arial", 9))
        gen_cb.pack(side='left', padx=6)
        make_flat_button(row2, '绑定到本部门', lambda: do_bind(), COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=13).pack(side='left', padx=4)
        make_flat_button(row2, '解绑（设为通用）', lambda: do_unbind(), COLOR_WARNING, COLOR_WARNING,
                         width=14).pack(side='left', padx=4)

        def refresh_dept_positions(dept_name):
            cur_dept['v'] = dept_name
            bound_lb.delete(0, 'end')
            if not dept_name:
                return
            bound, general = DB.dept_positions(dept_name)
            for b in bound:
                bound_lb.insert('end', b)
            gen_cb.configure(values=general if general else ['（无通用职位可绑定）'])
            gen_var.set(general[0] if general else '')

        def do_bind():
            p = gen_var.get()
            if not p or p.startswith('（'):
                messagebox.showwarning('提示', '请选择要绑定的通用职位'); return
            if not cur_dept['v']:
                messagebox.showwarning('提示', '请先在部门列表选择一个部门'); return
            DB.bind_position_dept(p, cur_dept['v'])
            DB.log(self.user['type'], self.user['id'], self.user['name'], '部门职位绑定',
                   f'职位《{p}》绑定到部门《{cur_dept["v"]}》')
            messagebox.showinfo('成功', f'职位《{p}》已绑定到《{cur_dept["v"]}》')
            refresh_dept_positions(cur_dept['v'])

        def do_unbind():
            sel = bound_lb.curselection()
            if not sel:
                messagebox.showwarning('提示', '请先在上方列表选中一个本部门职位'); return
            p = bound_lb.get(sel[0])
            DB.bind_position_dept(p, '')
            DB.log(self.user['type'], self.user['id'], self.user['name'], '部门职位绑定', f'职位《{p}》解绑为通用')
            messagebox.showinfo('成功', f'职位《{p}》已设为通用（可在任何部门任职）')
            refresh_dept_positions(cur_dept['v'])

        def refresh_parent_options(exclude=''):
            names = ['（顶级部门）'] + [r['name'] for r in DB.all_departments()
                                        if r['name'] != exclude and r['is_active']]
            parent_cb['values'] = names

        def load():
            for it in tree.get_children():
                tree.delete(it)
            for r in DB.all_departments():
                tree.insert('', 'end', values=(r['id'], r['name'], r['parent_name'] or '（顶级）',
                                               r['power_level'], r['head_name'] or '-',
                                               r['sort_order'], '启用' if r['is_active'] else '停用'))
            refresh_parent_options()
            refresh_dept_positions('')

        def clear_form():
            sel_id['v'] = None
            name_e.config(state='normal'); name_e.delete(0, 'end')
            parent_var.set('（顶级部门）')
            power_var.set('60'); sort_var.set('0'); head_e.delete(0, 'end')
            active_var.set(1)
            refresh_parent_options()
            refresh_dept_positions('')

        def on_select(_=None):
            vals = tree.item(tree.selection()[0])['values'] if tree.selection() else None
            if not vals:
                return
            did = int(vals[0])
            row = next((x for x in DB.all_departments() if x['id'] == did), None)
            if not row:
                return
            sel_id['v'] = did
            name_e.delete(0, 'end'); name_e.insert(0, row['name'])
            refresh_parent_options(exclude=row['name'])
            parent_var.set(row['parent_name'] or '（顶级部门）')
            power_var.set(str(row['power_level'])); sort_var.set(str(row['sort_order']))
            head_e.delete(0, 'end'); head_e.insert(0, row['head_name'] or '')
            active_var.set(int(row['is_active']))
            refresh_dept_positions(row['name'])
        tree.bind('<<TreeviewSelect>>', on_select)

        def validate_power():
            try:
                int(power_var.get()); int(sort_var.get())
            except ValueError:
                messagebox.showwarning('提示', '权力分值和排序必须为整数'); return False
            return True

        def save():
            name = name_e.get().strip()
            if not name:
                messagebox.showwarning('提示', '请填写部门名称'); return
            if not validate_power():
                return
            pname = '' if parent_var.get() == '（顶级部门）' else parent_var.get()
            # 防层级环：上级不能是自己或自己的下级
            if sel_id['v'] and pname:
                if pname == name or DB.dept_is_descendant(pname, name):
                    messagebox.showerror('层级错误', '不能将上级设为该部门自身或其下级，会形成循环。')
                    return
            try:
                DB.upsert_department(name, pname, int(power_var.get()), head_e.get().strip(),
                                     int(sort_var.get()), active_var.get(), sel_id['v'])
                DB.log(self.user['type'], self.user['id'], self.user['name'], '部门管理',
                       f'保存部门《{name}》上级={pname or "顶级"} 权力={power_var.get()}')
                messagebox.showinfo('成功', '部门已保存')
                load(); clear_form()
            except sqlite3.IntegrityError:
                messagebox.showerror('重复', '已存在同名部门')

        def toggle_active():
            if not sel_id['v']:
                messagebox.showwarning('提示', '请先在右侧选择部门'); return
            row = next((x for x in DB.all_departments() if x['id'] == sel_id['v']), None)
            newv = 0 if row['is_active'] else 1
            DB.upsert_department(row['name'], row['parent_name'] or '', row['power_level'],
                                 row['head_name'] or '', row['sort_order'], newv, row['id'])
            DB.log(self.user['type'], self.user['id'], self.user['name'], '部门管理',
                   f'部门《{row["name"]}》{"启用" if newv else "停用"}')
            load(); clear_form()

        bf = tk.Frame(form, bg=COLOR_CONTENT_BG)
        bf.grid(row=7, column=0, columnspan=2, pady=12)
        make_flat_button(bf, "➕ 新增", lambda: (clear_form(), name_e.focus_set()),
                         COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=9).pack(side='left', padx=4)
        make_flat_button(bf, "💾 保存", save, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=9).pack(side='left', padx=4)
        make_flat_button(bf, "启用/停用", toggle_active, COLOR_WARNING, COLOR_WARNING, width=10).pack(side='left', padx=4)
        load()

    # ========== 管理员：职级管理 ==========
    def _show_rank_mgmt(self):
        self._clear_main()
        make_page_title(self.main_frame, "🎖️ 职级管理（职级权力与默认权限）").pack(pady=15)

        top = tk.LabelFrame(self.main_frame, text="职级权力一览（选中后可在下方修改）",
                            font=("Arial", 12, "bold"), bg=COLOR_CONTENT_BG, padx=10, pady=8)
        top.pack(fill='x', padx=25)
        cols = ('id', '职级名称', '等级(越大越高)', '发送条款', '审批', '发文', '办件互动', '默认审批范围')
        tf, tree = make_tree(top, cols,
                             {'id': 40, '职级名称': 110, '等级(越大越高)': 110, '发送条款': 80,
                              '审批': 70, '发文': 70, '办件互动': 80, '默认审批范围': 150}, height=11)
        tf.pack(fill='x')

        edit = tk.LabelFrame(self.main_frame, text="修改职级默认权限（新增同职级官员自动套用）",
                             font=("Arial", 12, "bold"), bg=COLOR_CONTENT_BG, padx=15, pady=12)
        edit.pack(fill='x', padx=25, pady=12)
        sel = {'id': None}
        tk.Label(edit, text="职级：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=0, column=0, padx=5, pady=6, sticky='e')
        name_lab = tk.Label(edit, text='（请选择）', font=("Arial", 11, 'bold'), bg=COLOR_CONTENT_BG)
        name_lab.grid(row=0, column=1, sticky='w')
        tk.Label(edit, text="权力等级(1-11)：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=0, column=2, padx=(25, 5), sticky='e')
        grade_var = tk.StringVar()
        grade_sp = ttk.Spinbox(edit, from_=1, to=11, textvariable=grade_var, width=5, font=("Arial", 11))
        grade_sp.grid(row=0, column=3, sticky='w')

        perm_vars = {k: tk.IntVar() for k in PERM_KEYS}
        labels = {'send_clause': '发送条款', 'approve': '审批决定', 'send_doc': '公文发文', 'handle': '办件/互动'}
        for i, k in enumerate(PERM_KEYS):
            tk.Checkbutton(edit, text=labels[k], variable=perm_vars[k], bg=COLOR_CONTENT_BG,
                           font=("Arial", 11)).grid(row=1, column=i, padx=10, pady=8, sticky='w')
        tk.Label(edit, text="默认审批范围：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=2, column=0, padx=5, pady=6, sticky='e')
        scope_var = tk.StringVar()
        scope_opts = [('无审批权', 'none'), ('仅本部门', 'self'), ('本部门及下级', 'subtree'), ('全部部门', 'all')]
        sf = tk.Frame(edit, bg=COLOR_CONTENT_BG); sf.grid(row=2, column=1, columnspan=3, sticky='w')
        for txt, val in scope_opts:
            tk.Radiobutton(sf, text=txt, variable=scope_var, value=val, bg=COLOR_CONTENT_BG,
                           font=("Arial", 10)).pack(side='left', padx=8)

        def yn(v):
            return '✔' if v else '—'

        def load():
            for it in tree.get_children():
                tree.delete(it)
            for r in DB.all_ranks():
                tree.insert('', 'end', values=(r['id'], r['name'], r['grade'],
                                               yn(r['d_send_clause']), yn(r['d_approve']),
                                               yn(r['d_send_doc']), yn(r['d_handle']),
                                               SCOPE_LABELS.get(r['default_scope'], r['default_scope'])))

        def on_select(_=None):
            if not tree.selection():
                return
            rid = int(tree.item(tree.selection()[0])['values'][0])
            row = next((x for x in DB.all_ranks() if x['id'] == rid), None)
            if not row:
                return
            sel['id'] = rid
            name_lab.config(text=row['name'])
            grade_var.set(str(row['grade']))
            for k in PERM_KEYS:
                perm_vars[k].set(row[PERM_RANK_COL[k]])
            scope_var.set(row['default_scope'] if row['default_scope'] in
                          ('none', 'self', 'subtree', 'all') else 'none')
        tree.bind('<<TreeviewSelect>>', on_select)

        def save():
            if not sel['id']:
                messagebox.showwarning('提示', '请先在上方选择要修改的职级'); return
            try:
                grade = int(grade_var.get())
            except ValueError:
                messagebox.showwarning('提示', '等级必须为整数'); return
            row = next((x for x in DB.all_ranks() if x['id'] == sel['id']), None)
            if any(r['id'] != sel['id'] and r['grade'] == grade for r in DB.all_ranks()):
                messagebox.showerror('冲突', f'等级 {grade} 已被其他职级占用，等级必须唯一'); return
            approve_on = perm_vars['approve'].get()
            scope = scope_var.get()
            if not approve_on and scope != 'none':
                messagebox.showwarning('提示', '未授予“审批决定”权限时，默认审批范围只能为“无审批权”。'); return
            DB.save_rank_defaults(sel['id'], grade, row['name'],
                                  perm_vars['send_clause'].get(), approve_on,
                                  perm_vars['send_doc'].get(), perm_vars['handle'].get(), scope)
            DB.log('admin', self.user['id'], self.user['name'], '职级管理',
                   f'修改职级《{row["name"]}》等级={grade} 范围={SCOPE_LABELS[scope]}')
            messagebox.showinfo('成功', '职级默认权限已更新')
            load()

        make_flat_button(edit, "💾 保存职级设置", save, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=16).grid(
            row=3, column=0, columnspan=4, pady=10)
        load()

    # ========== 管理员：职务管理 ==========
    def _show_position_mgmt(self):
        self._clear_main()
        make_page_title(self.main_frame, "👔 职务管理（职务名称与职务权限）").pack(pady=12)

        top = tk.LabelFrame(self.main_frame, text="职务列表（选中后可在下方修改；职务权限介于“职级默认”与“官员个人”之间）",
                            font=("Arial", 12, "bold"), bg=COLOR_CONTENT_BG, padx=10, pady=6)
        top.pack(fill='x', padx=25)
        cols = ('id', '职务名称', '所属部门', '发送条款', '审批决定', '公文发文', '办件互动', '审批范围', '排序', '状态')
        tf, tree = make_tree(top, cols,
                             {'id': 40, '职务名称': 120, '所属部门': 110, '发送条款': 80, '审批决定': 80,
                              '公文发文': 80, '办件互动': 80, '审批范围': 110, '排序': 45, '状态': 55}, height=8)
        tf.pack(fill='x')

        edit = tk.LabelFrame(self.main_frame, text="职务信息与权限（“随职级”= 不额外限制，按官员职级默认）",
                             font=("Arial", 12, "bold"), bg=COLOR_CONTENT_BG, padx=18, pady=12)
        edit.pack(fill='both', expand=True, padx=25, pady=12)

        sel = {'id': None}
        MODE_TXT = {'default': '随职级', 'allow': '允许', 'deny': '禁止'}
        SCOPE_TXT = {'default': '随职级', 'none': '无审批权', 'self': '仅本部门',
                     'subtree': '本部门及下级', 'custom': '指定部门', 'all': '全部部门'}

        # 基本信息
        base_f = tk.Frame(edit, bg=COLOR_CONTENT_BG); base_f.pack(fill='x')
        tk.Label(base_f, text="职务名称：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(2, 4))
        name_var = tk.StringVar()
        tk.Entry(base_f, textvariable=name_var, font=("Arial", 11), width=16).pack(side='left', padx=4)
        tk.Label(base_f, text="所属部门：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(12, 4))
        dept_var = tk.StringVar(value='通用（任何部门）')
        _dept_opts = ['通用（任何部门）', '州部门（仅限各州）', '立法机构（议会/参议院/众议院）',
                      '司法机构（最高法院/联邦法院）'] + [d for d in DB.active_departments() if d]
        ttk.Combobox(base_f, textvariable=dept_var, values=_dept_opts, state='readonly',
                     width=22, font=("Arial", 10)).pack(side='left', padx=4)
        tk.Label(base_f, text="排序：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(12, 4))
        sort_var = tk.StringVar(value='10')
        ttk.Spinbox(base_f, from_=0, to=999, textvariable=sort_var, width=5,
                    font=("Arial", 11)).pack(side='left', padx=4)
        active_var = tk.IntVar(value=1)
        tk.Checkbutton(base_f, text="启用该职务", variable=active_var, bg=COLOR_CONTENT_BG,
                       font=("Arial", 11)).pack(side='left', padx=12)
        tk.Label(base_f, text="提示：任命官员时职务将按部门过滤，避免“在财政部当总统”",
                 font=("Arial", 9), fg=COLOR_TEXT_GRAY, bg=COLOR_CONTENT_BG).pack(side='left', padx=10)

        # 四权限三态
        body = tk.Frame(edit, bg=COLOR_CONTENT_BG); body.pack(fill='x', pady=10)
        hdr = tk.Frame(body, bg=COLOR_CONTENT_BG); hdr.pack(fill='x', pady=(4, 2))
        tk.Label(hdr, text='权限项', width=34, anchor='w', font=("Arial", 11, 'bold'),
                 bg=COLOR_CONTENT_BG).pack(side='left')
        for t in ('随职级', '允许', '禁止'):
            tk.Label(hdr, text=t, width=8, font=("Arial", 11, 'bold'), bg=COLOR_CONTENT_BG).pack(side='left')
        perm_modes = {}
        for k in PERM_KEYS:
            v = tk.StringVar(value='default'); perm_modes[k] = v
            r = tk.Frame(body, bg=COLOR_CONTENT_BG); r.pack(fill='x', pady=3)
            tk.Label(r, text=PERM_LABELS[k], width=34, anchor='w', font=("Arial", 10),
                     bg=COLOR_CONTENT_BG).pack(side='left')
            for val in ('default', 'allow', 'deny'):
                tk.Radiobutton(r, text='', variable=v, value=val, bg=COLOR_CONTENT_BG).pack(side='left', padx=18)

        # 审批范围
        sf = tk.LabelFrame(edit, text='审批范围（仅“审批决定”生效时有效）', font=("Arial", 11, 'bold'),
                           bg=COLOR_CONTENT_BG, padx=10, pady=8)
        sf.pack(fill='x', pady=6)
        scope_var = tk.StringVar(value='default')
        custom_vars = {}
        for txt, val in [('随职级默认', 'default'), ('无审批权', 'none'), ('仅本部门', 'self'),
                         ('本部门及下级', 'subtree'), ('指定部门', 'custom'), ('全部部门', 'all')]:
            tk.Radiobutton(sf, text=txt, variable=scope_var, value=val, bg=COLOR_CONTENT_BG,
                           font=("Arial", 10)).pack(side='left', padx=8)
        cf = tk.Frame(sf, bg=COLOR_CONTENT_BG); cf.pack(fill='x', pady=(8, 0))
        tk.Label(cf, text='指定部门（勾选）：', font=("Arial", 10), bg=COLOR_CONTENT_BG).pack(anchor='w')
        cl = tk.Frame(cf, bg=COLOR_CONTENT_BG); cl.pack(fill='x')
        for i, dn in enumerate(DB.active_departments()):
            cv = tk.IntVar(); custom_vars[dn] = cv
            tk.Checkbutton(cl, text=dn, variable=cv, bg=COLOR_CONTENT_BG, font=("Arial", 9)
                           ).grid(row=i // 5, column=i % 5, sticky='w', padx=6)

        def load():
            for it in tree.get_children():
                tree.delete(it)
            for r in DB.all_positions():
                tree.insert('', 'end', values=(
                    r['id'], r['name'], dept_display(r), MODE_TXT.get(r['p_send_clause'], r['p_send_clause']),
                    MODE_TXT.get(r['p_approve'], r['p_approve']),
                    MODE_TXT.get(r['p_send_doc'], r['p_send_doc']),
                    MODE_TXT.get(r['p_handle'], r['p_handle']),
                    SCOPE_TXT.get(r['p_scope'], r['p_scope']), r['sort_order'],
                    '启用' if r['is_active'] else '停用'))

        def dept_display(r):
            if r['department']:
                return r['department']
            k = r['dept_kind'] or ''
            return {'state': '州部门（仅限各州）', 'legislative': '立法机构',
                    'judicial': '司法机构'}.get(k, '通用（任何部门）')

        def dept_to_code(val):
            if val.startswith('州部门'):
                return '', 'state'
            if val.startswith('立法机构'):
                return '', 'legislative'
            if val.startswith('司法机构'):
                return '', 'judicial'
            if val == '通用（任何部门）':
                return '', ''
            return val, ''

        def clear_form():
            sel['id'] = None
            name_var.set(''); sort_var.set('10'); active_var.set(1); dept_var.set('通用（任何部门）')
            for v in perm_modes.values():
                v.set('default')
            scope_var.set('default')
            for cv in custom_vars.values():
                cv.set(0)

        def on_select(_=None):
            if not tree.selection():
                return
            pid = int(tree.item(tree.selection()[0])['values'][0])
            row = DB.get_position(pid)
            if not row:
                return
            sel['id'] = pid
            name_var.set(row['name']); sort_var.set(str(row['sort_order']))
            active_var.set(int(row['is_active']))
            dept_var.set(dept_display(row))
            for k, col in PERM_POS_COL.items():
                perm_modes[k].set(row[col] or 'default')
            sv = row['p_scope'] or 'default'
            scope_var.set(sv if sv in SCOPE_TXT else 'default')
            chosen = [x.strip() for x in (row['p_custom_depts'] or '').split(',') if x.strip()]
            for dn, cv in custom_vars.items():
                cv.set(1 if dn in chosen else 0)
        tree.bind('<<TreeviewSelect>>', on_select)

        def do_save():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning('提示', '请填写职务名称'); return
            try:
                sort_order = int(sort_var.get())
            except ValueError:
                messagebox.showwarning('提示', '排序必须为整数'); return
            sv = scope_var.get()
            chosen = []
            if sv == 'custom':
                chosen = [dn for dn, cv in custom_vars.items() if cv.get()]
                if not chosen:
                    messagebox.showwarning('提示', '选择“指定部门”时至少勾选一个部门'); return
            if perm_modes['approve'].get() == 'deny' and sv not in ('default', 'none'):
                if not messagebox.askyesno('确认', '该职务“审批决定”设为禁止，审批范围将不生效，确定保存？'):
                    return
            dep, kind = dept_to_code(dept_var.get())
            ok, msg = DB.save_position(name, perm_modes['send_clause'].get(),
                                       perm_modes['approve'].get(), perm_modes['send_doc'].get(),
                                       perm_modes['handle'].get(), sv, chosen, sort_order,
                                       active_var.get(), sel['id'], dep, kind)
            if not ok:
                messagebox.showerror('保存失败', msg); return
            DB.log('admin', self.user['id'], self.user['name'], '职务管理',
                   f"{'修改' if sel['id'] else '新增'}职务《{name}》 部门={dept_display({'department': dep, 'dept_kind': kind})} 范围={SCOPE_TXT[sv]}")
            messagebox.showinfo('成功', msg)
            load()
            # 选中刚保存的行
            target = None
            for it in tree.get_children():
                if str(tree.item(it)['values'][1]) == name:
                    target = it; break
            if target:
                tree.selection_set(target); tree.focus(target); on_select()

        def do_new():
            clear_form(); name_var.focus_set()

        def toggle_active():
            if not sel['id']:
                messagebox.showwarning('提示', '请先选择职务'); return
            row = DB.get_position(sel['id'])
            new_active = 0 if row['is_active'] else 1
            ok, msg = DB.save_position(row['name'], row['p_send_clause'], row['p_approve'],
                                       row['p_send_doc'], row['p_handle'], row['p_scope'],
                                       [x.strip() for x in (row['p_custom_depts'] or '').split(',') if x.strip()],
                                       row['sort_order'], new_active, sel['id'])
            if ok:
                DB.log('admin', self.user['id'], self.user['name'], '职务管理',
                       f"职务《{row['name']}》{'启用' if new_active else '停用'}")
                messagebox.showinfo('成功', f"已{'启用' if new_active else '停用'}")
                load(); on_select()

        def do_delete():
            if not sel['id']:
                messagebox.showwarning('提示', '请先选择要删除的职务'); return
            row = DB.get_position(sel['id'])
            if not messagebox.askyesno('确认删除', f'确定删除职务“{row["name"]}”吗？'):
                return
            ok, msg = DB.delete_position(sel['id'])
            if not ok:
                messagebox.showwarning('无法删除', msg); return
            DB.log('admin', self.user['id'], self.user['name'], '职务管理', f"删除职务《{row['name']}》")
            messagebox.showinfo('成功', msg); clear_form(); load()

        bf = tk.Frame(edit, bg=COLOR_CONTENT_BG); bf.pack(pady=8)
        make_flat_button(bf, "➕ 新增", do_new, COLOR_GRAY, COLOR_TEXT_GRAY, width=10).pack(side='left', padx=6)
        make_flat_button(bf, "💾 保存职务", do_save, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12).pack(side='left', padx=6)
        make_flat_button(bf, "🔁 启用/停用", toggle_active, COLOR_WARNING, COLOR_WARNING, width=12).pack(side='left', padx=6)
        make_flat_button(bf, "🎛️ 该职务44项权力", lambda: self._position_power_dialog(sel['id']),
                         COLOR_PURPLE, COLOR_PURPLE, width=14).pack(side='left', padx=6)
        make_flat_button(bf, "🗑️ 删除职务", do_delete, COLOR_DANGER, COLOR_DANGER_HOVER, width=12).pack(side='left', padx=6)
        load()

    # ---------- 职务 44 项权力点编辑弹窗 ----------
    def _position_power_dialog(self, pid):
        if not pid:
            messagebox.showwarning('提示', '请先在上方选择一个职务'); return
        row = DB.get_position(pid)
        if not row:
            messagebox.showwarning('提示', '职务不存在'); return
        win = tk.Toplevel(self.root)
        win.title(f'该职务可用的权力 - {row["name"]}')
        win.geometry('760x640')
        win.transient(self.root); win.grab_set()

        hdr = tk.Frame(win, bg='#f0f1f5'); hdr.pack(fill='x', padx=0, pady=0)
        tk.Label(hdr, text=f"《{row['name']}》可用权力（44 项 · 随职级=不额外限制，按职级默认）",
                 font=("Arial", 12, "bold"), bg='#f0f1f5').pack(side='left', padx=15, pady=10)

        wrap = tk.Frame(win); wrap.pack(fill='both', expand=True, padx=10, pady=6)
        canv = tk.Canvas(wrap, bg='white', highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient='vertical', command=canv.yview)
        canv.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        canv.pack(side='left', fill='both', expand=True)
        body = tk.Frame(canv, bg='white')
        body_win = canv.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda e: canv.configure(scrollregion=canv.bbox('all')))
        canv.bind('<Configure>', lambda e: canv.itemconfig(body_win, width=e.width))
        def _wheel(e):
            canv.yview_scroll(int(-1 * (e.delta / 120)), 'units')
        canv.bind('<MouseWheel>', _wheel)

        powers = DB.power_catalog()
        cur = DB.position_power_map(pid)
        group_order = [(BRANCH_ADMIN, '⚖️ 行政权'), (BRANCH_LEGISLATE, '📜 立法权'),
                       (BRANCH_JUDICIARY, '🧑‍⚖️ 司法权'), ('党派', '🎭 党派权'),
                       (BRANCH_STATE, '🗺️ 州权'), ('通用', '🔧 通用权')]
        vars_map = {}
        MODE_TXT = {'default': '随职级', 'allow': '允许', 'deny': '禁止'}
        for gkey, gtitle in group_order:
            items = [p for p in powers if p.get('branch') == gkey]
            if not items:
                continue
            tk.Label(body, text=gtitle, font=("Arial", 11, "bold"), bg='white',
                     fg=COLOR_TITLE).pack(anchor='w', pady=(8, 2))
            hdr_r = tk.Frame(body, bg='white'); hdr_r.pack(fill='x')
            tk.Label(hdr_r, text='权力点', width=40, anchor='w', font=("Arial", 10, 'bold'),
                     bg='white').pack(side='left')
            for t in ('随职级', '允许', '禁止'):
                tk.Label(hdr_r, text=t, width=7, font=("Arial", 10, 'bold'), bg='white').pack(side='left')
            for p in items:
                v = tk.StringVar(value=cur.get(p['pkey'], 'default'))
                vars_map[p['pkey']] = v
                r = tk.Frame(body, bg='white'); r.pack(fill='x', pady=2)
                tk.Label(r, text=f"{p['name']}", width=40, anchor='w', font=("Arial", 9),
                         bg='white', fg='#333').pack(side='left')
                for val in ('default', 'allow', 'deny'):
                    tk.Radiobutton(r, text='', variable=v, value=val, bg='white').pack(side='left', padx=13)

        def do_save():
            n = 0
            for k, v in vars_map.items():
                DB.set_position_power(pid, k, v.get())
                n += 1
            DB.log('admin', self.user['id'], self.user['name'], '职务权限',
                   f"配置职务《{row['name']}》{n} 项权力")
            messagebox.showinfo('成功', f'已保存职务《{row["name"]}》的 {n} 项权力配置')
            win.destroy()

        def do_reset():
            if not messagebox.askyesno('确认', '恢复该职务全部权力为“随职级”（清除 44 项覆盖）？'):
                return
            DB.clear_position_powers(pid)
            DB.log('admin', self.user['id'], self.user['name'], '职务权限', f"恢复职务《{row['name']}》为随职级")
            messagebox.showinfo('成功', '已恢复为随职级默认')
            win.destroy()

        bf = tk.Frame(win); bf.pack(pady=8)
        make_flat_button(bf, '💾 保存该职务权力', do_save, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=16).pack(side='left', padx=8)
        make_flat_button(bf, '↩️ 恢复随职级', do_reset, COLOR_WARNING, COLOR_WARNING, width=14).pack(side='left', padx=8)
        make_flat_button(bf, '关闭', win.destroy, COLOR_GRAY, COLOR_TEXT_GRAY, width=10).pack(side='left', padx=8)

    # ========== 管理员：官员个人权限管理 ==========
    def _show_permission_mgmt(self):
        self._clear_main()
        make_page_title(self.main_frame, "🔑 权限管理（按官员单独微调）").pack(pady=12)

        top = tk.LabelFrame(self.main_frame, text="选择官员", font=("Arial", 12, "bold"),
                            bg=COLOR_CONTENT_BG, padx=10, pady=6)
        top.pack(fill='x', padx=25)
        cols = ('id', '姓名', '身份证号', '部门', '职务', '职级', '状态')
        tf, tree = make_tree(top, cols, {'id': 40, '姓名': 90, '身份证号': 160, '部门': 100,
                                         '职务': 90, '职级': 100, '状态': 60}, height=6)
        tf.pack(fill='x')

        cfg = tk.LabelFrame(self.main_frame, text="权限配置（“跟随默认”= 按职级/职务自动授予；允许/禁止为单独覆盖）",
                            font=("Arial", 12, "bold"), bg=COLOR_CONTENT_BG, padx=18, pady=14)
        cfg.pack(fill='both', expand=True, padx=25, pady=12)

        info_lab = tk.Label(cfg, text='请先在上方选择一名官员', font=("Arial", 12, 'bold'),
                            bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY)
        info_lab.pack(anchor='w')
        result_lab = tk.Label(cfg, text='', font=("Arial", 11, 'bold'), bg='#f4f8ff',
                              fg=COLOR_TITLE, justify='left', anchor='w')

        # 可滚动主体
        canv_wrap = tk.Frame(cfg, bg=COLOR_CONTENT_BG)
        canv_wrap.pack(fill='both', expand=True, pady=6)
        canv = tk.Canvas(canv_wrap, bg=COLOR_CONTENT_BG, highlightthickness=0)
        sb = tk.Scrollbar(canv_wrap, orient='vertical', command=canv.yview)
        canv.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        canv.pack(side='left', fill='both', expand=True)
        body = tk.Frame(canv, bg=COLOR_CONTENT_BG)
        body_win = canv.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda e: canv.configure(scrollregion=canv.bbox('all')))
        canv.bind('<Configure>', lambda e: canv.itemconfig(body_win, width=e.width))
        def _wheel(e):
            canv.yview_scroll(int(-1 * (e.delta / 120)), 'units')
        canv.bind('<MouseWheel>', _wheel)

        perm_modes = {}
        scope_var = tk.StringVar(value='default')
        custom_vars = {}
        power_vars = {}   # V2 权力点：pkey -> StringVar('default'/'allow'/'deny')

        def build_body():
            # 表头：旧四项基础权限
            hdr = tk.Frame(body, bg=COLOR_CONTENT_BG); hdr.pack(fill='x', pady=(4, 2))
            tk.Label(hdr, text='权限项', width=34, anchor='w', font=("Arial", 11, 'bold'),
                     bg=COLOR_CONTENT_BG).pack(side='left')
            for t in ('跟随默认', '允许', '禁止'):
                tk.Label(hdr, text=t, width=8, font=("Arial", 11, 'bold'),
                         bg=COLOR_CONTENT_BG).pack(side='left')
            for k in PERM_KEYS:
                v = tk.StringVar(value='default')
                perm_modes[k] = v
                r = tk.Frame(body, bg=COLOR_CONTENT_BG); r.pack(fill='x', pady=2)
                tk.Label(r, text=PERM_LABELS[k], width=34, anchor='w', font=("Arial", 10),
                         bg=COLOR_CONTENT_BG).pack(side='left')
                for val in ('default', 'allow', 'deny'):
                    tk.Radiobutton(r, text='', variable=v, value=val, bg=COLOR_CONTENT_BG,
                                   command=refresh_result).pack(side='left', padx=18)
            # 审批范围
            sf = tk.LabelFrame(body, text='审批范围（仅“审批决定”生效时有效）', font=("Arial", 11, 'bold'),
                               bg=COLOR_CONTENT_BG, padx=10, pady=6)
            sf.pack(fill='x', pady=8)
            for txt, val in [('跟随职级默认', 'default'), ('仅本部门', 'self'),
                             ('本部门及下级', 'subtree'), ('指定部门', 'custom'), ('全部部门', 'all')]:
                tk.Radiobutton(sf, text=txt, variable=scope_var, value=val, bg=COLOR_CONTENT_BG,
                               font=("Arial", 10), command=refresh_result).pack(side='left', padx=8)
            cf = tk.Frame(sf, bg=COLOR_CONTENT_BG); cf.pack(fill='x', pady=(6, 0))
            tk.Label(cf, text='指定部门（勾选）：', font=("Arial", 10), bg=COLOR_CONTENT_BG).pack(anchor='w')
            cl = tk.Frame(cf, bg=COLOR_CONTENT_BG); cl.pack(fill='x')
            for i, dn in enumerate(DB.active_departments()):
                cv = tk.IntVar(); custom_vars[dn] = cv
                tk.Checkbutton(cl, text=dn, variable=cv, bg=COLOR_CONTENT_BG, font=("Arial", 9),
                               command=refresh_result).grid(row=i // 5, column=i % 5, sticky='w', padx=6)
            # ===== V2 美式权力点（44 项，按分支分组）=====
            tk.Label(body, text="── 三权分立权力点（44 项，可单独覆盖）──",
                     font=("Arial", 11, 'bold'), bg=COLOR_CONTENT_BG, fg=COLOR_PURPLE).pack(pady=(10, 2))
            branch_order = [BRANCH_ADMIN, BRANCH_LEGISLATE, BRANCH_JUDICIARY, '党派', BRANCH_STATE, '通用']
            cat = DB.power_catalog()
            for br in branch_order:
                items = [x for x in cat if x['branch'] == br]
                if not items:
                    continue
                bf2 = tk.LabelFrame(body, text=f"【{br}权】", font=("Arial", 10, 'bold'),
                                    bg=COLOR_CONTENT_BG, padx=8, pady=4)
                bf2.pack(fill='x', pady=4)
                for idx, it in enumerate(items):
                    pk = it['pkey']
                    if pk in PERM_KEYS:
                        continue  # 旧四项已在上方展示
                    v = tk.StringVar(value='default')
                    power_vars[pk] = v
                    row = idx // 2
                    col = (idx % 2) * 5
                    cell = tk.Frame(bf2, bg=COLOR_CONTENT_BG)
                    cell.grid(row=row, column=col, columnspan=5, sticky='w', padx=4, pady=1)
                    tk.Label(cell, text=it['name'], width=24, anchor='w', font=("Arial", 9),
                             bg=COLOR_CONTENT_BG).pack(side='left')
                    for val in ('default', 'allow', 'deny'):
                        tk.Radiobutton(cell, text='', variable=v, value=val, bg=COLOR_CONTENT_BG,
                                       command=refresh_result).pack(side='left', padx=8)

        def refresh_result():
            if not tree.selection():
                return
            oid = int(tree.item(tree.selection()[0])['values'][0])
            official = self._get_official(oid)
            if not official:
                return
            pinfo = DB.official_perm(official)
            preview = dict(pinfo['eff'])
            for k, v in perm_modes.items():
                if v.get() == 'allow':
                    preview[k] = True
                elif v.get() == 'deny':
                    preview[k] = False
            for pk, v in power_vars.items():
                if v.get() == 'allow':
                    preview[pk] = True
                elif v.get() == 'deny':
                    preview[pk] = False
            sv = scope_var.get()
            scope_eff = pinfo['scope'] if sv == 'default' else sv
            if not preview['approve']:
                scope_eff = 'none'
            scope_txt = SCOPE_LABELS.get(scope_eff, scope_eff)
            names = {'send_clause': '发送条款', 'approve': '审批决定', 'send_doc': '公文发文', 'handle': '办件/互动'}
            line = '　'.join(f"{names[k]}:{'✔' if preview[k] else '—'}" for k in PERM_KEYS)
            # 显示生效中的美式权力点
            pow_on = [it['name'] for it in DB.power_catalog()
                      if it['pkey'] not in PERM_KEYS and preview.get(it['pkey'])]
            extra = ('；权力点：' + '、'.join(pow_on[:14]) + ('…' if len(pow_on) > 14 else '')) if pow_on else '；权力点：无'
            result_lab.config(text=f"最终生效 →  {line}　|　审批范围：{scope_txt}{extra}")

        def load_official():
            if not tree.selection():
                return
            oid = int(tree.item(tree.selection()[0])['values'][0])
            official = self._get_official(oid)
            if not official:
                return
            pinfo = DB.official_perm(official)
            info_lab.config(text=f"当前官员：{official['name']}　|　部门：{official['department']}"
                                 f"（权力分值 {pinfo['dept_level'] if pinfo['dept_level'] is not None else '-'}）"
                                 f"　职务：{official['position']}　职级：{official['rank']}"
                                 f"（等级 {pinfo['grade'] if pinfo['grade'] is not None else '-'}）")
            for k in PERM_KEYS:
                perm_modes[k].set(pinfo['overrides'].get(k, 'default'))
            ov = DB.official_power_overrides(oid)
            for pk in power_vars:
                power_vars[pk].set(ov.get(pk, 'default'))
            # 范围回显：若存在覆盖则按覆盖，否则 default
            conn = DB.get_conn(GOV_DB)
            so = conn.execute("SELECT scope,custom_depts FROM official_scope_overrides WHERE official_id=?",
                              (oid,)).fetchone()
            conn.close()
            if so:
                scope_var.set(so['scope'])
                chosen = [x.strip() for x in (so['custom_depts'] or '').split(',') if x.strip()]
            else:
                scope_var.set('default'); chosen = []
            for dn, cv in custom_vars.items():
                cv.set(1 if dn in chosen else 0)
            result_lab.pack(fill='x', padx=2, pady=6)
            refresh_result()

        def save():
            if not tree.selection():
                messagebox.showwarning('提示', '请先选择官员'); return
            oid = int(tree.item(tree.selection()[0])['values'][0])
            official = self._get_official(oid)
            for k, v in perm_modes.items():
                DB.set_official_override(oid, k, v.get() if v.get() in ('allow', 'deny') else None)
            for pk, v in power_vars.items():
                DB.set_official_power(oid, pk, v.get() if v.get() in ('allow', 'deny') else 'remove')
            sv = scope_var.get()
            if sv == 'custom':
                chosen = [dn for dn, cv in custom_vars.items() if cv.get()]
                if not chosen:
                    messagebox.showwarning('提示', '选择“指定部门”时至少勾选一个部门'); return
                DB.set_official_scope(oid, 'custom', chosen)
            elif sv == 'default':
                DB.set_official_scope(oid, None)
            else:
                DB.set_official_scope(oid, sv, [])
            DB.log('admin', self.user['id'], self.user['name'], '权限管理',
                   f"调整官员{official['name']}权限，范围={scope_var.get()}")
            messagebox.showinfo('成功', '权限已保存并立即生效')
            load_official()

        def reset():
            if not tree.selection():
                messagebox.showwarning('提示', '请先选择官员'); return
            oid = int(tree.item(tree.selection()[0])['values'][0])
            if not messagebox.askyesno('确认', '确定清除该官员的全部个人覆盖（含 44 项权力点），恢复为按部门+职级+职务的默认权限？'):
                return
            DB.reset_official_perms(oid)
            DB.log('admin', self.user['id'], self.user['name'], '权限管理',
                   f"恢复官员 oid={oid} 默认权限")
            messagebox.showinfo('成功', '已恢复默认权限')
            load_official()

        bf = tk.Frame(cfg, bg=COLOR_CONTENT_BG); bf.pack(pady=4)
        make_flat_button(bf, "💾 保存权限", save, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12).pack(side='left', padx=8)
        make_flat_button(bf, "↩️ 恢复默认", reset, COLOR_WARNING, COLOR_WARNING, width=12).pack(side='left', padx=8)
        build_body()

        def load_officials():
            for it in tree.get_children():
                tree.delete(it)
            conn = DB.get_conn(GOV_DB)
            for r in conn.execute("SELECT * FROM officials "
                                  "ORDER BY CASE WHEN position='副部长' THEN 32 "
                                  "ELSE COALESCE((SELECT sort_order FROM positions WHERE name=officials.position),999) END, "
                                  "department, id").fetchall():
                tree.insert('', 'end', values=(r['id'], r['name'], r['id_number'], r['department'],
                                               r['position'], r['rank'], r['status']))
            conn.close()
        tree.bind('<<TreeviewSelect>>', lambda e: load_official())
        load_officials()

    def _show_exec_order(self):
        """总统端：签署/发布行政令（可立即或定时生效，精确到分钟；可撤回）。"""
        if (self.user.get('position') or '') != '总统':
            messagebox.showwarning('提示', '仅“总统”身份可签署/发布行政令。')
            return
        if not self._can('exec_order'):
            messagebox.showwarning('无操作权限',
                                   '您的账号没有“签发行政令”权限。\n如需开通请联系管理员在“权限管理”中调整。')
            return
        self._clear_main()
        make_page_title(self.main_frame, "📜 签署 / 发布行政令（总统）").pack(pady=15)

        form_f = tk.LabelFrame(self.main_frame, text="发布新行政令", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=20, pady=10)
        form_f.pack(fill='x', padx=30, pady=5)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG); row1.pack(fill='x', pady=5)
        tk.Label(row1, text="标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row1, width=44); title_e.pack(side='left', padx=5)

        tk.Label(form_f, text="行政令正文：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(
            anchor='w', padx=5, pady=(8, 0))
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=7, wrap='word')
        content_e.pack(fill='x', padx=5, pady=5)

        eff_f = tk.Frame(form_f, bg=COLOR_CONTENT_BG); eff_f.pack(fill='x', pady=4)
        tk.Label(eff_f, text="生效方式：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        eff_var = tk.StringVar(value='立即生效')
        tk.Radiobutton(eff_f, text="立即生效", variable=eff_var, value='立即生效',
                       font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=8)
        tk.Radiobutton(eff_f, text="定时生效（精确到分钟）", variable=eff_var, value='定时生效',
                       font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=8)

        time_f = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        tk.Label(time_f, text="生效时间：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        now_dt = datetime.now()
        y_var = tk.StringVar(value=str(now_dt.year))
        mo_var = tk.StringVar(value=f"{now_dt.month:02d}")
        d_var = tk.StringVar(value=f"{now_dt.day:02d}")
        h_var = tk.StringVar(value=f"{now_dt.hour:02d}")
        mi_var = tk.StringVar(value=f"{now_dt.minute:02d}")
        ttk.Combobox(time_f, textvariable=y_var, values=[str(y) for y in range(now_dt.year, now_dt.year + 11)],
                     font=("Arial", 11), width=6, state='readonly').pack(side='left', padx=2)
        tk.Label(time_f, text="年", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left')
        ttk.Combobox(time_f, textvariable=mo_var, values=[f'{m:02d}' for m in range(1, 13)],
                     font=("Arial", 11), width=5, state='readonly').pack(side='left', padx=2)
        tk.Label(time_f, text="月", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left')
        ttk.Combobox(time_f, textvariable=d_var, values=[f'{dd:02d}' for dd in range(1, 32)],
                     font=("Arial", 11), width=5, state='readonly').pack(side='left', padx=2)
        tk.Label(time_f, text="日", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left')
        ttk.Combobox(time_f, textvariable=h_var, values=[f'{hh:02d}' for hh in range(0, 24)],
                     font=("Arial", 11), width=5, state='readonly').pack(side='left', padx=2)
        tk.Label(time_f, text="时", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left')
        ttk.Combobox(time_f, textvariable=mi_var, values=[f'{mm:02d}' for mm in range(0, 60)],
                     font=("Arial", 11), width=5, state='readonly').pack(side='left', padx=2)
        tk.Label(time_f, text="分", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left')

        def _toggle_time():
            if eff_var.get() == '定时生效':
                time_f.pack(fill='x', pady=4)
            else:
                time_f.pack_forget()
        _toggle_time()
        eff_var.trace_add('write', lambda *a: _toggle_time())

        def submit():
            if not self._can('exec_order'):
                messagebox.showwarning('无操作权限', '您的账号没有“签发行政令”权限。')
                return
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写行政令标题和正文内容')
                return
            eff_mode = eff_var.get()
            eff_time = f"{y_var.get()}-{mo_var.get()}-{d_var.get()} {h_var.get()}:{mi_var.get()}"
            ok, msg = DB.create_executive_order(title, content, self.user, eff_mode, eff_time)
            if not ok:
                messagebox.showwarning('提示', msg)
                return
            messagebox.showinfo('成功', f'行政令《{title}》已签署并发布（{msg}）\n'
                                        f'生效方式：{eff_mode}，生效时间：{eff_time}')
            title_e.delete(0, 'end'); content_e.delete('1.0', 'end')
            load()

        make_flat_button(form_f, "✍️ 签署并发布", submit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=14).pack(pady=8)

        list_f = tk.LabelFrame(self.main_frame, text="我签署的行政令", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill='both', expand=True, padx=30, pady=8)
        cols = ('id', '编号', '标题', '生效方式', '生效时间', '状态', '发布时间')
        tf, tree = make_tree(list_f, cols, {'id': 40, '编号': 140, '标题': 260, '生效方式': 90,
                                            '生效时间': 140, '状态': 80, '发布时间': 140}, height=10)
        tf.pack(fill='both', expand=True)
        tree.tag_configure('已生效', foreground='#2e7d32')
        tree.tag_configure('待生效', foreground='#1565c0')
        tree.tag_configure('已撤回', foreground='#c62828')

        def load():
            for it in tree.get_children():
                tree.delete(it)
            for r in DB.list_executive_orders():
                if r['publisher_id'] != self.user['id']:
                    continue
                tree.insert('', 'end', values=(r['id'], r['order_no'], r['title'], r['effective_mode'],
                                                r['effective_time'], r['status'], r['created_at']),
                            tags=(r['status'],))
        load()

        def view_detail():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条行政令')
                return
            r = DB.get_executive_order(int(tree.item(sel[0])['values'][0]))
            if not r:
                messagebox.showwarning('提示', '行政令不存在')
                return
            info = [('编号', r['order_no']), ('标题', r['title']),
                    ('发布人', f"{r['publisher_name']}（{r['publisher_position']}）"),
                    ('生效方式', r['effective_mode']), ('生效时间', r['effective_time']),
                    ('当前状态', r['status']), ('发布时间', r['created_at'])]
            if r['is_withdrawn']:
                info.append(('撤回时间', r['withdrawn_at'] or '-'))
                info.append(('撤回人', r['withdrawn_by'] or '-'))
            self._popup_detail(f'行政令详情 - {r["order_no"]}', info, '行政令全文：', r['content'])

        def withdraw():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条行政令')
                return
            r = DB.get_executive_order(int(tree.item(sel[0])['values'][0]))
            if not r:
                messagebox.showwarning('提示', '行政令不存在')
                return
            if r['is_withdrawn']:
                messagebox.showwarning('提示', '该行政令已撤回，不能重复撤回')
                return
            if not messagebox.askyesno('确认撤回',
                                       f'确定撤回行政令《{r["title"]}》（{r["order_no"]}）吗？\n'
                                       f'撤回后将在政务公开中显示“已撤回”。'):
                return
            ok, msg = DB.withdraw_executive_order(r['id'], self.user)
            if not ok:
                messagebox.showwarning('提示', msg)
                return
            messagebox.showinfo('成功', f'行政令《{r["title"]}》已撤回')
            load()

        op_f = tk.Frame(list_f, bg=COLOR_CONTENT_BG)
        op_f.pack(pady=6)
        make_flat_button(op_f, '📄 查看详情', view_detail, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=12, height=1).pack(side='left', padx=6)
        make_flat_button(op_f, '↩️ 撤回行政令', withdraw, COLOR_DANGER, COLOR_DANGER,
                         width=12, height=1).pack(side='left', padx=6)
        tree.bind('<Double-1>', lambda e: view_detail())

    def _show_exec_order_mgmt(self):
        """总统端：行政令管理——可查看并撤回全部行政令（含前任总统发布的）。"""
        if (self.user.get('position') or '') != '总统':
            messagebox.showwarning('提示', '仅“总统”身份可进入行政令管理。')
            return
        if not self._can('exec_order'):
            messagebox.showwarning('无操作权限',
                                   '您的账号没有“签发行政令”权限，无法进行行政令管理。\n'
                                   '如需开通请联系管理员在“权限管理”中调整。')
            return
        self._clear_main()
        make_page_title(self.main_frame, "📜 行政令管理（全部行政令）").pack(pady=15)

        filter_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        filter_f.pack(pady=5)
        tk.Label(filter_f, text="关键词：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        kw_e = make_groove_entry(filter_f, width=22)
        kw_e.pack(side='left', padx=5)

        list_f = tk.LabelFrame(self.main_frame, text="全部行政令（含前任总统发布）", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=25, pady=8)
        cols = ('id', '编号', '标题', '发布人', '生效方式', '生效时间', '状态', '发布时间')
        tf, tree = make_tree(list_f, cols, {'id': 40, '编号': 140, '标题': 260, '发布人': 100,
                                            '生效方式': 90, '生效时间': 140, '状态': 80,
                                            '发布时间': 140}, height=12)
        tf.pack(fill=tk.BOTH, expand=True)
        tree.tag_configure('已生效', foreground='#2e7d32')
        tree.tag_configure('待生效', foreground='#1565c0')
        tree.tag_configure('已撤回', foreground='#c62828')

        def load():
            for item in tree.get_children():
                tree.delete(item)
            kw = kw_e.get().strip()
            for r in DB.list_executive_orders():
                if kw and kw not in r['title'] and kw not in r['order_no'] and kw not in r['content']:
                    continue
                tree.insert('', 'end', values=(r['id'], r['order_no'], r['title'],
                                               f"{r['publisher_name']}（{r['publisher_position']}）",
                                               r['effective_mode'], r['effective_time'],
                                               r['status'], r['created_at']),
                            tags=(r['status'],))

        load()
        tk.Button(filter_f, text="查询", command=load, width=8, bg=COLOR_PRIMARY, fg='white',
                  relief=tk.FLAT, cursor='hand2').pack(side='left', padx=5)

        def view_detail():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条行政令')
                return
            r = DB.get_executive_order(int(tree.item(sel[0])['values'][0]))
            if not r:
                messagebox.showwarning('提示', '行政令不存在')
                return
            info = [('编号', r['order_no']), ('标题', r['title']),
                    ('发布人', f"{r['publisher_name']}（{r['publisher_position']}）"),
                    ('生效方式', r['effective_mode']), ('生效时间', r['effective_time']),
                    ('当前状态', r['status']), ('发布时间', r['created_at'])]
            if r['is_withdrawn']:
                info.append(('撤回时间', r['withdrawn_at'] or '-'))
                info.append(('撤回人', r['withdrawn_by'] or '-'))
            self._popup_detail(f'行政令详情 - {r["order_no"]}', info, '行政令全文：', r['content'])

        def withdraw():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条行政令')
                return
            r = DB.get_executive_order(int(tree.item(sel[0])['values'][0]))
            if not r:
                messagebox.showwarning('提示', '行政令不存在')
                return
            if r['is_withdrawn']:
                messagebox.showwarning('提示', '该行政令已撤回，不能重复撤回')
                return
            pub = f"{r['publisher_name']}（{'现任总统' if r['publisher_id'] == self.user['id'] else '前任总统'}）"
            if not messagebox.askyesno('确认撤回',
                                       f'确定撤回行政令《{r["title"]}》（{r["order_no"]}）吗？\n'
                                       f'发布人：{pub}\n撤回后将在政务公开中显示“已撤回”。'):
                return
            ok, msg = DB.withdraw_executive_order(r['id'], self.user, manage=True)
            if not ok:
                messagebox.showwarning('提示', msg)
                return
            messagebox.showinfo('成功', f'行政令《{r["title"]}》已撤回')
            load()

        op_f = tk.Frame(list_f, bg=COLOR_CONTENT_BG)
        op_f.pack(pady=6)
        make_flat_button(op_f, '📄 查看详情', view_detail, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=12, height=1).pack(side='left', padx=6)
        make_flat_button(op_f, '↩️ 撤回行政令', withdraw, COLOR_DANGER, COLOR_DANGER,
                         width=12, height=1).pack(side='left', padx=6)
        make_flat_button(op_f, '🔄 刷新', lambda: self._show_exec_order_mgmt(), COLOR_GRAY, COLOR_GRAY,
                         width=10, height=1).pack(side='left', padx=6)
        tree.bind('<Double-1>', lambda e: view_detail())

    # ========== 总统：法律发布 ==========
    def _show_law_publish(self):
        self._clear_main()
        make_page_title(self.main_frame, "📜 法律发布（总统）").pack(pady=10)

        # 先跑一次自动计票，保证列表新鲜
        try:
            DB.law_tick()
        except Exception:
            pass

        top = tk.Frame(self.main_frame, bg='white')
        top.pack(fill='x', padx=15, pady=(0, 8))

        def propose_dialog():
            win = tk.Toplevel(self.root)
            win.title("起草新法律")
            win.geometry("640x560")
            win.configure(bg='white')
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text="📜 起草新法律 / 修改法律", font=("Arial", 14, "bold"),
                     bg='white', fg=COLOR_PRIMARY).pack(pady=10)
            form = tk.Frame(win, bg='white')
            form.pack(fill='x', padx=20)
            tk.Label(form, text="标题：", bg='white').grid(row=0, column=0, sticky='w', pady=4)
            e_title = tk.Entry(form, width=55)
            e_title.grid(row=0, column=1, pady=4)
            tk.Label(form, text="法条正文：", bg='white').grid(row=1, column=0, sticky='nw', pady=4)
            t_content = tk.Text(form, width=55, height=10, font=("Arial", 10))
            t_content.grid(row=1, column=1, pady=4)
            tk.Label(form, text="投票截止：", bg='white').grid(row=2, column=0, sticky='w', pady=4)
            e_deadline = tk.Entry(form, width=55)
            e_deadline.insert(0, (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d %H:%M'))
            e_deadline.grid(row=2, column=1, pady=4)
            tk.Label(form, text="生效日期：", bg='white').grid(row=3, column=0, sticky='w', pady=4)
            e_eff = tk.Entry(form, width=55)
            e_eff.insert(0, (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d'))
            e_eff.grid(row=3, column=1, pady=4)
            tk.Label(form, text="（格式 YYYY-MM-DD HH:MM / YYYY-MM-DD）", bg='white',
                     fg=COLOR_GRAY).grid(row=4, column=1, sticky='w')

            def submit():
                ok, msg, lid = DB.propose_law(
                    e_title.get().strip(),
                    t_content.get('1.0', 'end').strip(),
                    e_deadline.get().strip(),
                    e_eff.get().strip(),
                    self.user['id'], self.user['name'])
                if not ok:
                    messagebox.showerror("错误", msg, parent=win)
                    return
                messagebox.showinfo("成功", msg, parent=win)
                win.destroy()
                self._show_law_publish()
            make_flat_button(win, "✅ 提交议会投票", submit, COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(pady=12)

        make_flat_button(top, "➕ 起草新法律", propose_dialog, COLOR_PRIMARY, COLOR_PRIMARY, width=16).pack(side='left')
        tk.Label(top, text="到投票截止自动计票；通过后在此确认发布，系统自动签总统行政令。",
                 bg='white', fg=COLOR_GRAY).pack(side='left', padx=15)

        # 待发布列表
        tk.Label(self.main_frame, text="━━ 待发布（已通过议会投票） ━━", bg='white',
                 font=("Arial", 11, "bold"), fg=COLOR_SUCCESS).pack(anchor='w', padx=15, pady=(8, 2))
        pend = tk.Frame(self.main_frame, bg='white')
        pend.pack(fill='x', padx=15)
        laws_pending = [l for l in DB.list_laws() if l['status'] == '已通过待发布']
        if not laws_pending:
            tk.Label(pend, text="暂无待发布法律", bg='white', fg=COLOR_GRAY).pack(anchor='w')
        for l in laws_pending:
            row = tk.Frame(pend, bg='#f0fff0', bd=1, relief='solid')
            row.pack(fill='x', pady=3, ipady=4)
            tk.Label(row, text=f"《{l['title']}》  生效 {l['effective_date']}", bg='#f0fff0',
                     font=("Arial", 10, "bold")).pack(side='left', padx=8)
            tk.Label(row, text=f"赞成 {l['yes_count']} / 反对 {l['no_count']} / 弃权 {l['abstain_count']}",
                     bg='#f0fff0', fg=COLOR_GRAY).pack(side='left', padx=8)

            def do_publish(lid=l['id']):
                if not messagebox.askyesno("确认发布",
                                           f"确认发布《{l['title']}》？\n系统将自动以您的名义签署总统行政令。"):
                    return
                ok, msg = DB.confirm_publish_law(lid, self.user['id'], self.user['name'])
                if not ok:
                    messagebox.showerror("错误", msg)
                else:
                    messagebox.showinfo("已发布", msg)
                self._show_law_publish()
            make_flat_button(row, "📜 确认发布", do_publish, COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(side='right', padx=8)

        # 全部法律
        tk.Label(self.main_frame, text="━━ 全部法律 ━━", bg='white',
                 font=("Arial", 11, "bold")).pack(anchor='w', padx=15, pady=(12, 2))
        cols = ('id', 'title', 'proposer', 'deadline', 'effective', 'status', 'votes')
        tree = ttk.Treeview(self.main_frame, columns=cols, show='headings', height=12)
        for c, t, w in [('id', 'ID', 50), ('title', '法律标题', 220), ('proposer', '提案人', 90),
                        ('deadline', '投票截止', 130), ('effective', '生效日', 100),
                        ('status', '状态', 110), ('votes', '赞成/反对/弃权', 130)]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor='center')
        tree.pack(fill='both', expand=True, padx=15, pady=5)
        all_laws = DB.list_laws()
        for l in all_laws:
            tree.insert('', 'end', values=(
                l['id'], l['title'], l['proposer_name'], l['vote_deadline'],
                l['effective_date'], l['status'],
                f"{l['yes_count']}/{l['no_count']}/{l['abstain_count']}"))

        def _selected_law():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("提示", "请先在上方列表选中一条法律")
                return None
            lid = int(tree.item(sel[0], 'values')[0])
            return next((l for l in all_laws if l['id'] == lid), None)

        def do_tally_publish():
            l = _selected_law()
            if not l:
                return
            if not messagebox.askyesno("确认计票并发布",
                                       f"立即结束投票并对《{l['title']}》计票？\n"
                                       f"通过则直接签发总统行政令（生效日 {l['effective_date']}）。"):
                return
            ok, msg, _ = DB.tally_and_publish(l['id'], self.user['id'], self.user['name'])
            if ok:
                messagebox.showinfo("发布成功", msg)
            else:
                messagebox.showwarning("未通过", msg)
            self._show_law_publish()

        def do_amend():
            l = _selected_law()
            if not l:
                return
            # 弹起草窗，预填原标题/法条
            win = tk.Toplevel(self.root)
            win.title(f"修改法律：《{l['title']}》")
            win.geometry("640x560")
            win.configure(bg='white')
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text=f"✏️ 修改法律（原法 id={l['id']}）", font=("Arial", 14, "bold"),
                     bg='white', fg=COLOR_PRIMARY).pack(pady=10)
            form = tk.Frame(win, bg='white')
            form.pack(fill='x', padx=20)
            tk.Label(form, text="新标题：", bg='white').grid(row=0, column=0, sticky='w', pady=4)
            e_title = tk.Entry(form, width=55)
            e_title.insert(0, l['title'])
            e_title.grid(row=0, column=1, pady=4)
            tk.Label(form, text="修改后法条：", bg='white').grid(row=1, column=0, sticky='nw', pady=4)
            t_content = tk.Text(form, width=55, height=10, font=("Arial", 10))
            t_content.insert('1.0', l['content'])
            t_content.grid(row=1, column=1, pady=4)
            tk.Label(form, text="投票截止：", bg='white').grid(row=2, column=0, sticky='w', pady=4)
            e_deadline = tk.Entry(form, width=55)
            e_deadline.insert(0, (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d %H:%M'))
            e_deadline.grid(row=2, column=1, pady=4)
            tk.Label(form, text="生效日期：", bg='white').grid(row=3, column=0, sticky='w', pady=4)
            e_eff = tk.Entry(form, width=55)
            e_eff.insert(0, (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d'))
            e_eff.grid(row=3, column=1, pady=4)

            def submit():
                ok, msg, _ = DB.propose_law(
                    e_title.get().strip(),
                    t_content.get('1.0', 'end').strip(),
                    e_deadline.get().strip(),
                    e_eff.get().strip(),
                    self.user['id'], self.user['name'], amend_of=l['id'])
                if not ok:
                    messagebox.showerror("错误", msg, parent=win)
                    return
                messagebox.showinfo("成功", msg, parent=win)
                win.destroy()
                self._show_law_publish()
            make_flat_button(win, "✅ 提交议会投票（修改案）", submit,
                             COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(pady=12)

        btn_bar = tk.Frame(self.main_frame, bg='white')
        btn_bar.pack(fill='x', padx=15, pady=(0, 10))
        make_flat_button(btn_bar, "✅ 选中并确认发布（立即计票）", do_tally_publish,
                         COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=24).pack(side='left', padx=4)
        make_flat_button(btn_bar, "✏️ 修改选中法律", do_amend,
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=18).pack(side='left', padx=4)
        tk.Label(btn_bar, text="先在上方列表点选一行，再点按钮；生效日仍按法律填写的日期生效。",
                 bg='white', fg=COLOR_GRAY).pack(side='left', padx=15)

    # ========== 议员：法律投票 ==========
    def _show_law_vote(self):
        self._clear_main()
        make_page_title(self.main_frame, "📜 法律投票（议会）").pack(pady=10)
        try:
            DB.law_tick()
        except Exception:
            pass
        tk.Label(self.main_frame, text="下列法律正等待议会审批。请逐条投票。",
                 bg='white', fg=COLOR_GRAY).pack(anchor='w', padx=15, pady=(0, 8))
        canv = tk.Frame(self.main_frame, bg='white')
        canv.pack(fill='both', expand=True, padx=15)
        laws = [l for l in DB.list_laws() if l['status'] == '投票中']
        if not laws:
            tk.Label(canv, text="当前无待投票法律", bg='white', fg=COLOR_GRAY,
                     font=("Arial", 12)).pack(pady=40)
            return
        # 查本人已投票
        conn = DB.get_conn(GOV_DB)
        my_votes = {r['law_id']: r['vote'] for r in
                    conn.execute("SELECT law_id, vote FROM law_votes WHERE official_id=?",
                                 (self.user['id'],)).fetchall()}
        conn.close()
        for l in laws:
            card = tk.Frame(canv, bg='#f8f9ff', bd=1, relief='solid')
            card.pack(fill='x', pady=5, ipady=6)
            head = tk.Frame(card, bg='#f8f9ff')
            head.pack(fill='x', padx=10)
            tk.Label(head, text=f"《{l['title']}》", bg='#f8f9ff', font=("Arial", 12, "bold"),
                     fg=COLOR_PRIMARY).pack(side='left')
            tk.Label(head, text=f"  截止 {l['vote_deadline']}  生效 {l['effective_date']}",
                     bg='#f8f9ff', fg=COLOR_GRAY).pack(side='left', padx=10)
            tk.Label(card, text=l['content'][:300] + ('…' if len(l['content']) > 300 else ''),
                     bg='#f8f9ff', justify='left', wraplength=800).pack(anchor='w', padx=10, pady=4)
            foot = tk.Frame(card, bg='#f8f9ff')
            foot.pack(fill='x', padx=10, pady=4)
            my_v = my_votes.get(l['id'], '')
            tk.Label(foot, text=f"您已投：{my_v or '未投票'}", bg='#f8f9ff',
                     fg=COLOR_SUCCESS if my_v else COLOR_GRAY).pack(side='left', padx=5)

            def do_vote(v, lid=l['id']):
                ok, msg = DB.law_vote(lid, self.user['id'], self.user['name'], v)
                if not ok:
                    messagebox.showerror("错误", msg)
                else:
                    messagebox.showinfo("投票成功", msg)
                self._show_law_vote()
            make_flat_button(foot, "👍 赞成", lambda: do_vote('赞成'),
                             COLOR_SUCCESS, COLOR_SUCCESS_HOVER).pack(side='left', padx=4)
            make_flat_button(foot, "👎 反对", lambda: do_vote('反对'),
                             COLOR_DANGER, COLOR_DANGER_HOVER).pack(side='left', padx=4)
            make_flat_button(foot, "⚪ 弃权", lambda: do_vote('弃权'),
                             COLOR_GRAY, COLOR_GRAY).pack(side='left', padx=4)

    # ========== 总统：保释罪犯（对接监狱管理系统·商业版） ==========
    def _show_bail_prisoners(self):
        """总统端：按身份证号/囚号/姓名搜索在押罪犯，可批量保释；
        保释信息发送至监狱管理系统（商业版），并自动发布一版保释行政令（生效日期=保释日期）。"""
        if (self.user.get('position') or '') != '总统':
            messagebox.showwarning('提示', '仅“总统”身份可执行保释罪犯操作。')
            return
        if not self._can('exec_order'):
            messagebox.showwarning('无操作权限',
                                   '您的账号没有“签发行政令”权限。\n保释罪犯需配套发布行政令，'
                                   '请联系管理员在“权限管理”中调整。')
            return
        self._clear_main()
        make_page_title(self.main_frame, "⛓️ 保释罪犯（总统 · 对接监狱管理系统商务版）").pack(pady=15)

        # ---- 搜索栏 ----
        top_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        top_f.pack(fill=tk.X, padx=30, pady=4)
        tk.Label(top_f, text='搜索罪犯（身份证号 / 囚号 / 姓名）：', font=("Arial", 11),
                 bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        kw_e = make_groove_entry(top_f, width=26)
        kw_e.pack(side='left', padx=5)

        list_f = tk.LabelFrame(self.main_frame, text="在押罪犯列表（Ctrl/Shift 多选可批量保释）",
                               font=("Arial", 12, "bold"), bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=8)
        cols = ('id', '囚号', '身份证号', '姓名', '性别', '案由', '刑期(月)', '预计释放', '状态')
        tree_frame, tree = make_tree(list_f, cols, {'id': 36, '囚号': 120, '身份证号': 150, '姓名': 70,
                                           '性别': 40, '案由': 130, '刑期(月)': 70, '预计释放': 90,
                                           '状态': 60}, height=11)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        tree.configure(selectmode='extended')

        lbl_cnt = tk.Label(top_f, text='', font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY)
        lbl_cnt.pack(side='left', padx=10)

        def do_search(_=None):
            rows = DB.search_prisoners(kw_e.get().strip())
            for it in tree.get_children():
                tree.delete(it)
            for r in rows:
                tree.insert('', 'end', values=(
                    r['id'], r['inmate_no'], r['id_number'], r['name'], r['gender'] or '',
                    r['case_name'] or r['case_type'] or '', r['sentence_months'],
                    (r['expected_release_date'] or '')[:10], r['status']))
            lbl_cnt.config(text=f'共检索到 {len(rows)} 名在押罪犯')

        def show_all():
            kw_e.delete(0, 'end')
            do_search()

        kw_e.bind('<Return>', do_search)
        make_flat_button(top_f, '🔍 搜索', do_search, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(side='left', padx=5)
        make_flat_button(top_f, '显示全部在押', show_all, COLOR_GRAY, COLOR_GRAY,
                         width=12, height=1).pack(side='left', padx=5)
        do_search()

        # ---- 保释信息 ----
        form_f = tk.LabelFrame(self.main_frame, text="保释信息", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=16, pady=8)
        form_f.pack(fill=tk.X, padx=30, pady=8)
        rf = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        rf.pack(fill=tk.X, pady=4)
        tk.Label(rf, text='保释原因：', font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        reason_e = make_groove_entry(rf, width=40)
        reason_e.pack(side='left', padx=5)
        tk.Label(rf, text='保释日期（YYYY-MM-DD）：', font=("Arial", 11),
                 bg=COLOR_CONTENT_BG).pack(side='left', padx=10)
        date_e = make_groove_entry(rf, width=13)
        date_e.insert(0, datetime.now().strftime('%Y-%m-%d'))
        date_e.pack(side='left', padx=5)
        tk.Label(form_f, text='保释后将把保释信息发送至监狱管理系统（商业版），并自动发布一版保释行政令，'
                              '行政令生效日期即保释日期。',
                 font=("Arial", 9), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(anchor='w', padx=5, pady=(0, 4))

        def do_bail():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择要保释的罪犯（可 Ctrl/Shift 多选批量保释）')
                return
            reason = reason_e.get().strip()
            bail_date = date_e.get().strip()
            try:
                datetime.strptime(bail_date, '%Y-%m-%d')
            except Exception:
                messagebox.showwarning('提示', '保释日期格式不正确（YYYY-MM-DD）')
                return
            if not reason:
                messagebox.showwarning('提示', '请填写保释原因')
                return
            rows = []
            for s in sel:
                v = tree.item(s)['values']
                rows.append({'pid': v[0], 'inmate_no': v[1], 'id_number': v[2], 'name': v[3]})
            names = '、'.join(r['name'] for r in rows)
            if not messagebox.askyesno(
                    '确认保释',
                    f'确定对以下 {len(rows)} 名罪犯予以保释吗？\n\n{names}\n\n'
                    f'保释原因：{reason}\n保释日期：{bail_date}\n\n'
                    f'确认后将把保释信息发送至监狱管理系统（商业版），并自动发布一版'
                    f'关于保释罪犯的行政令（生效日期=保释日期）。'):
                return
            ok, msg, done, _names = DB.bail_prisoners(
                [r['pid'] for r in rows], reason, bail_date, self.user['name'])
            if not ok:
                messagebox.showwarning('提示', msg)
                return
            title = '关于保释罪犯的行政令'
            content = (
                f'为彰显司法人道关怀、促进在押罪犯改过自新，依据宪法与总统职权，特发布本行政令：\n\n'
                f'一、对下列在押罪犯予以保释（共 {done} 名）：\n'
                + '\n'.join(f'　{i + 1}. {r["name"]}（囚号 {r["inmate_no"]}，身份证号 {r["id_number"]}）'
                            for i, r in enumerate(rows))
                + f'\n\n二、保释原因：{reason}\n'
                f'三、本行政令自 {bail_date} 起生效。\n\n'
                f'此令。\n总统：{self.user["name"]}\n{bail_date}'
            )
            ok2, order_no = DB.create_bail_executive_order(title, content, self.user, f'{bail_date} 00:00')
            if ok2:
                messagebox.showinfo('保释成功',
                                    f'已向监狱管理系统（商业版）发送保释信息，{done} 名罪犯获保释。\n\n'
                                    f'已自动发布保释行政令：《{title}》\n令号：{order_no}\n生效日期：{bail_date}')
            else:
                messagebox.showwarning('保释成功，但行政令发布失败',
                                       f'已向监狱管理系统发送保释信息，{done} 名罪犯获保释。\n'
                                       f'行政令发布失败：{order_no}')
            reason_e.delete(0, 'end')
            do_search()
            load_records()

        # ---- 保释记录（监狱管理系统） ----
        rec_f = tk.LabelFrame(self.main_frame, text="保释记录（来自监狱管理系统）", font=("Arial", 12, "bold"),
                              bg=COLOR_CONTENT_BG, padx=10, pady=8)
        rec_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=8)
        rcols = ('id', '囚号', '身份证号', '姓名', '保释日期', '原因', '经办人', '记录时间')
        rt_frame, rtree = make_tree(rec_f, rcols, {'id': 40, '囚号': 110, '身份证号': 150, '姓名': 70,
                                          '保释日期': 90, '原因': 210, '经办人': 90, '记录时间': 130}, height=6)
        rt_frame.pack(fill=tk.BOTH, expand=True)

        def load_records():
            for it in rtree.get_children():
                rtree.delete(it)
            for r in DB.list_bail_records():
                rtree.insert('', 'end', values=(r['id'], r['inmate_no'], r['id_number'], r['name'],
                                                r['release_date'], r['reason'], r['operator'],
                                                (r['created_at'] or '')[:16]))
        load_records()

        op_f = tk.Frame(list_f, bg=COLOR_CONTENT_BG)
        op_f.pack(pady=6)
        make_flat_button(op_f, '⛓️ 批量保释', do_bail, COLOR_PURPLE, COLOR_PURPLE,
                         width=14, height=1).pack(side='left', padx=6)
        make_flat_button(op_f, '🔄 刷新', lambda: (do_search(), load_records()), COLOR_GRAY, COLOR_GRAY,
                         width=10, height=1).pack(side='left', padx=6)

    def _get_official(self, oid):
        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT * FROM officials WHERE id=?", (oid,)).fetchone()
        conn.close()
        return r

    # ========== 官员：发送条款（起草呈批件送审） ==========
    def _show_send_clause(self):
        if not self._guard('send_clause', '发送条款'):
            return
        self._clear_main()
        make_page_title(self.main_frame, "📝 发送条款（起草呈批件 / 条款送审）").pack(pady=15)

        form_f = tk.LabelFrame(self.main_frame, text="起草条款 / 呈批件", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=20, pady=10)
        form_f.pack(fill='x', padx=30, pady=5)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG); row1.pack(fill='x', pady=5)
        tk.Label(row1, text="标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row1, width=34); title_e.pack(side='left', padx=5)
        tk.Label(row1, text="类型：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        type_var = tk.StringVar(value='呈批件')
        ttk.Combobox(row1, textvariable=type_var, values=['呈批件', '条款', '请示', '报告', '其他'],
                     font=("Arial", 11), width=10, state='readonly').pack(side='left')
        tk.Label(row1, text="送审部门：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        dept_var = tk.StringVar(value=self.user['department'] if self.user['department'] in DB.active_departments()
                                else (DB.active_departments()[0] if DB.active_departments() else ''))
        ttk.Combobox(row1, textvariable=dept_var, values=DB.active_departments(), font=("Arial", 11),
                     width=10, state='readonly').pack(side='left')
        tk.Label(form_f, text="条款 / 正文内容：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(
            anchor='w', padx=5, pady=(8, 0))
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=7, wrap='word')
        content_e.pack(fill='x', padx=5, pady=5)

        def submit():
            if not self._guard('send_clause', '发送条款'):
                return
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写标题和正文内容'); return
            if not dept_var.get():
                messagebox.showwarning('提示', '请选择送审部门'); return
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO approval_files (title,content,file_type,applicant_id,applicant_name,
                applicant_type,target_department,status,created_at) VALUES (?,?,?,?,?,?,?, '待审批', ?)''',
                (title, content, type_var.get(), self.user['id_number'], self.user['name'],
                 '政府官员', dept_var.get(), now_str()))
            conn.commit()
            conn.close()
            DB.log('official', self.user['id'], self.user['name'], '发送条款',
                   f"向{dept_var.get()}送审《{title}》（{type_var.get()}）")
            messagebox.showinfo('成功', f'条款《{title}》已发送至【{dept_var.get()}】，等待有审批权限的官员审批。')
            title_e.delete(0, 'end'); content_e.delete('1.0', 'end')
            load_records()

        make_flat_button(form_f, "📨 发送条款", submit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14).pack(pady=8)

        list_f = tk.LabelFrame(self.main_frame, text="我发起的条款 / 呈批", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill='both', expand=True, padx=30, pady=8)
        cols = ('id', '标题', '类型', '送审部门', '状态', '审批人', '审批意见', '发送时间')
        tf, tree = make_tree(list_f, cols, {'id': 40, '标题': 200, '送审部门': 100,
                                            '审批意见': 180, '发送时间': 140}, height=10)
        tf.pack(fill='both', expand=True)

        def load_records():
            for it in tree.get_children():
                tree.delete(it)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute('''SELECT * FROM approval_files WHERE applicant_id=? AND applicant_type='政府官员'
                                   ORDER BY id DESC''', (self.user['id_number'],)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], r['file_type'],
                                               r['target_department'] or '未指定', r['status'],
                                               r['approver_name'] or '未审批',
                                               r['approve_comment'] or '-', r['created_at']))
        load_records()

    # ========== 民众：办事服务大厅 ==========
    def _show_service_hall(self):
        self._clear_main()
        make_page_title(self.main_frame, "🏛️ 办事服务大厅").pack(pady=20)
        info_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        info_f.pack(fill=tk.X, padx=30)
        tk.Label(info_f, text="选择办事事项，在线提交申请，全流程网上办理。", font=("Arial", 11),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(anchor='w')

        filter_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        filter_f.pack(pady=8)
        tk.Label(filter_f, text="事项分类：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        cat_var = tk.StringVar(value='全部')
        ttk.Combobox(filter_f, textvariable=cat_var, values=['全部'] + SERVICE_CATEGORIES,
                      font=("Arial", 11), width=12, state='readonly').pack(side='left', padx=5)
        tk.Label(filter_f, text="关键词：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        kw_e = make_groove_entry(filter_f, width=20)
        kw_e.pack(side='left', padx=5)

        list_frame = tk.LabelFrame(self.main_frame, text="服务事项目录", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        # 注意：list_frame 的 pack 延后到按钮行之后，确保操作按钮始终可见
        cols = ('id', '事项编号', '事项名称', '分类', '承办部门', '办理时限', '收费标准')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '事项编号': 80, '事项名称': 220,
                                                '分类': 90, '承办部门': 80, '办理时限': 100, '收费标准': 100}, height=14)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load_items():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            sql = "SELECT * FROM service_items WHERE is_active=1"
            params = []
            cat = cat_var.get()
            if cat != '全部':
                sql += " AND category=?"
                params.append(cat)
            kw = kw_e.get().strip()
            if kw:
                sql += " AND (name LIKE ? OR item_code LIKE ? OR department LIKE ?)"
                k = f'%{kw}%'
                params += [k, k, k]
            sql += " ORDER BY item_code"
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['item_code'], r['name'], r['category'],
                                                r['department'], r['processing_days'], r['fee']))

        load_items()
        tk.Button(filter_f, text="查询", command=load_items, width=8, bg=COLOR_PRIMARY, fg='white',
                  relief=tk.FLAT, cursor='hand2').pack(side='left', padx=5)

        def view_item():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一项办事事项')
                return
            iid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_items WHERE id=?", (iid,)).fetchone()
            conn.close()
            content = f"【事项说明】\n{r['description']}\n\n【所需材料】\n{r['required_materials']}\n\n【法律依据】\n{r['legal_basis']}"
            self._popup_detail(f'事项详情 - {r["name"]}',
                               [('事项编号', r['item_code']), ('事项名称', r['name']), ('分类', r['category']),
                                ('承办部门', r['department']), ('办理时限', r['processing_days']), ('收费标准', r['fee'])],
                               '详细信息：', content)

        def apply_item():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一项办事事项')
                return
            iid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            item = conn.execute("SELECT * FROM service_items WHERE id=?", (iid,)).fetchone()
            conn.close()
            if not item:
                return
            win = tk.Toplevel(self.root)
            win.title(f'在线申办 - {item["name"]}')
            win.geometry('620x640')
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text=f'事项：{item["name"]}', font=("Arial", 14, "bold")).pack(pady=10)
            tk.Label(win, text=f"承办部门：{item['department']} | 办理时限：{item['processing_days']} | 收费：{item['fee']}",
                     font=("Arial", 10), fg=COLOR_TEXT_GRAY).pack()
            hh = DB.get_household_info(self.user['id_number'])
            addr = (hh.get('address') or '') if hh else ''
            form_f = tk.Frame(win)
            form_f.pack(fill='x', padx=20, pady=10)
            tk.Label(form_f, text="联系电话：", font=("Arial", 11)).grid(row=0, column=0, sticky='e', pady=6, padx=5)
            phone_e = make_groove_entry(form_f, width=30)
            phone_e.grid(row=0, column=1, pady=6)
            tk.Label(form_f, text="联系地址：", font=("Arial", 11)).grid(row=1, column=0, sticky='e', pady=6, padx=5)
            addr_e = make_groove_entry(form_f, width=30)
            addr_e.insert(0, addr)
            addr_e.grid(row=1, column=1, pady=6)
            tk.Label(form_f, text="申请说明/备注：", font=("Arial", 11)).grid(row=2, column=0, sticky='ne', pady=6, padx=5)
            content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=8, width=40, wrap='word')
            content_e.grid(row=2, column=1, pady=6)

            def submit_case():
                content = content_e.get('1.0', 'end').strip()
                if not content:
                    messagebox.showwarning('提示', '请填写申请说明')
                    return
                case_no = gen_no('DB')
                conn = DB.get_conn(GOV_DB)
                conn.execute('''INSERT INTO service_cases (case_no,item_id,item_code,item_name,category,
                    applicant_id,applicant_name,applicant_phone,applicant_address,department,content,status,progress,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (case_no, item['id'], item['item_code'], item['name'], item['category'],
                     self.user['id_number'], self.user['name'], phone_e.get().strip(), addr_e.get().strip(),
                     item['department'], content, '待受理',
                     f"{now_str()} 提交申请，等待受理。", now_str()))
                conn.commit()
                conn.close()
                DB.log('citizen', self.user['id_number'], self.user['name'], '在线申办',
                       f'申办事项《{item["name"]}》，办件编号{case_no}')
                DB.send_message('citizen', self.user['id_number'], '办件受理通知',
                                f'您的事项《{item["name"]}》已提交成功，办件编号：{case_no}，请等待承办部门受理。')
                messagebox.showinfo('申办成功', f'申办成功！\n\n办件编号：{case_no}\n承办部门：{item["department"]}\n\n可在"我的办件"或"办件进度查询"中跟踪进度。')
                win.destroy()
                load_items()

            make_flat_button(win, '📨 提交申办', submit_case, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=16).pack(pady=10)

        # 操作按钮放在表格上方，确保始终可见
        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=8)
        make_flat_button(btn_f, '📄 事项详情', view_item, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '📨 在线申办', apply_item, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '📝 提交审批申请', lambda: self._show_my_applications(), COLOR_WARNING, COLOR_WARNING, width=16, height=1).pack(side='left', padx=5)
        tk.Label(self.main_frame, text="下方列表中选择事项后，点击上方按钮办理。", font=("Arial", 10),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack()
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)

    # ========== 民众：我的办件 ==========
    def _show_my_cases(self):
        self._clear_main()
        make_page_title(self.main_frame, "📋 我的办件").pack(pady=20)
        cid = self.user['id_number']

        list_frame = tk.LabelFrame(self.main_frame, text="我的申办记录", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        # 注意：list_frame 的 pack 延后到按钮行之后，确保操作按钮始终可见
        cols = ('id', '办件编号', '事项名称', '承办部门', '状态', '提交时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '办件编号': 160, '事项名称': 220,
                                                '承办部门': 90, '状态': 90, '提交时间': 140}, height=13)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM service_cases WHERE applicant_id=? ORDER BY id DESC", (cid,)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['case_no'], r['item_name'], r['department'],
                                                r['status'], r['created_at']))

        load()

        def view_progress():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条办件')
                return
            cid2 = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE id=?", (cid2,)).fetchone()
            conn.close()
            content = f"【申请内容】\n{r['content']}\n\n【办理进度】\n{r['progress'] or '暂无'}\n\n【处理意见】\n{r['handle_comment'] or '暂无'}"
            self._popup_detail(f'办件进度 - {r["case_no"]}',
                               [('办件编号', r['case_no']), ('事项', r['item_name']), ('承办部门', r['department']),
                                ('当前状态', r['status']), ('提交时间', r['created_at']), ('办结时间', r['finished_at'] or '-')],
                               '详细信息：', content)

        def rate_case():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条办件')
                return
            cid2 = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE id=?", (cid2,)).fetchone()
            conn.close()
            if r['status'] != '已办结':
                messagebox.showinfo('提示', '仅已办结的办件可进行服务评价')
                return
            if r['rating'] and r['rating'] > 0:
                messagebox.showinfo('提示', '您已评价过该办件')
                return
            win = tk.Toplevel(self.root)
            win.title(f'服务评价 - {r["case_no"]}')
            win.geometry('420x330')
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text=f'事项：{r["item_name"]}', font=("Arial", 13, "bold")).pack(pady=10)
            tk.Label(win, text='请对本次政务服务作出评价：', font=("Arial", 11)).pack()
            star_var = tk.IntVar(value=5)
            star_f = tk.Frame(win)
            star_f.pack(pady=8)
            for s in range(1, 6):
                tk.Radiobutton(star_f, text='★' * s, variable=star_var, value=s,
                               font=("Arial", 14), fg='#f39c12', bg='white').pack(side='left', padx=3)
            tk.Label(win, text='评价留言：').pack()
            cmt_e = scrolledtext.ScrolledText(win, font=("Arial", 11), height=4, width=36, wrap='word')
            cmt_e.pack(pady=5)

            def do_rate():
                conn = DB.get_conn(GOV_DB)
                conn.execute("UPDATE service_cases SET rating=?, rating_comment=? WHERE id=?",
                             (star_var.get(), cmt_e.get('1.0', 'end').strip(), cid2))
                conn.commit()
                conn.close()
                DB.log('citizen', self.user['id_number'], self.user['name'], '服务评价',
                       f'办件{r["case_no"]}评价{star_var.get()}星')
                messagebox.showinfo('成功', '评价提交成功，感谢您的反馈！')
                win.destroy()

            make_flat_button(win, '✅ 提交评价', do_rate, COLOR_WARNING, COLOR_WARNING, width=14).pack(pady=10)

        def withdraw_case():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条办件')
                return
            cid2 = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE id=?", (cid2,)).fetchone()
            conn.close()
            if r['status'] != '待受理':
                messagebox.showinfo('提示', '仅"待受理"状态的办件可撤回')
                return
            if not messagebox.askyesno('确认撤回', f'确定撤回办件 {r["case_no"]} 吗？'):
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute("DELETE FROM service_cases WHERE id=?", (cid2,))
            conn.commit()
            conn.close()
            DB.log('citizen', self.user['id_number'], self.user['name'], '撤回办件', f'撤回办件{r["case_no"]}')
            messagebox.showinfo('成功', '办件已撤回')
            load()

        def book_case_appt():
            """为选中的办件预约办理"""
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条办件')
                return
            cid2 = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE id=?", (cid2,)).fetchone()
            conn.close()
            if not r:
                messagebox.showwarning('提示', '办件不存在或已撤回')
                return
            if r['status'] == '已办结':
                messagebox.showinfo('提示', '该办件已办结，无需预约')
                return
            self._open_appointment_dialog(item_id=r['item_id'], item_name=r['item_name'],
                                          department=r['department'], case_no=r['case_no'])

        # 操作按钮放在表格上方，确保始终可见
        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=8)
        make_flat_button(btn_f, '📄 查看进度', view_progress, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '📅 预约办理', book_case_appt, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '⭐ 服务评价', rate_case, COLOR_WARNING, COLOR_WARNING, width=12, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '↩️ 撤回办件', withdraw_case, COLOR_DANGER, COLOR_DANGER_HOVER, width=12, height=1).pack(side='left', padx=5)
        tk.Label(self.main_frame, text="在下方列表中选择一条办件后，点击上方按钮操作。", font=("Arial", 10),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack()
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)

    # ========== 民众：办事预约 ==========
    def _show_my_appointments(self):
        self._clear_main()
        make_page_title(self.main_frame, "📅 办事预约").pack(pady=20)

        conn = DB.get_conn(GOV_DB)
        items = conn.execute("SELECT id,name,department FROM service_items WHERE is_active=1 ORDER BY name").fetchall()
        conn.close()
        item_names = [f"{i['name']}（{i['department']}）" for i in items]

        form_f = tk.LabelFrame(self.main_frame, text="在线预约", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=15, pady=10)
        form_f.pack(fill=tk.X, padx=30, pady=5)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=5)
        tk.Label(row1, text="预约事项：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        item_var = tk.StringVar()
        ttk.Combobox(row1, textvariable=item_var, values=item_names, font=("Arial", 11),
                     width=32, state='readonly').pack(side='left', padx=5)
        tk.Label(row1, text="预约日期(YYYY-MM-DD)：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(15, 5))
        date_e = make_groove_entry(row1, width=14)
        date_e.pack(side='left', padx=5)
        tk.Label(row1, text="时段：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(15, 5))
        slot_var = tk.StringVar(value=APPT_SLOTS[0])
        ttk.Combobox(row1, textvariable=slot_var, values=APPT_SLOTS, font=("Arial", 11),
                     width=14, state='readonly').pack(side='left', padx=5)

        def submit_appt():
            if not item_var.get():
                messagebox.showwarning('提示', '请选择预约事项')
                return
            date = date_e.get().strip()
            if not date:
                messagebox.showwarning('提示', '请填写预约日期')
                return
            idx = item_names.index(item_var.get())
            it = items[idx]
            appt_no = gen_no('YY')
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO service_appointments (appt_no,item_id,item_name,applicant_id,
                applicant_name,applicant_phone,appt_date,appt_time,department,status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (appt_no, it['id'], it['name'], self.user['id_number'], self.user['name'], '',
                 date, slot_var.get(), it['department'], '已预约', now_str()))
            conn.commit()
            conn.close()
            DB.log('citizen', self.user['id_number'], self.user['name'], '办事预约',
                   f'预约事项《{it["name"]}》{date} {slot_var.get()}')
            DB.send_message('citizen', self.user['id_number'], '预约成功通知',
                            f'您已成功预约《{it["name"]}》，时间：{date} {slot_var.get()}，预约号：{appt_no}')
            messagebox.showinfo('预约成功', f'预约成功！\n\n预约号：{appt_no}\n事项：{it["name"]}\n时间：{date} {slot_var.get()}\n请按时到场办理。')
            load_appts()

        make_flat_button(form_f, '📅 提交预约', submit_appt, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14).pack(pady=8)

        list_f = tk.LabelFrame(self.main_frame, text="我的预约", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '预约号', '事项', '预约日期', '时段', '状态', '提交时间')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '预约号': 150, '事项': 220,
                                            '预约日期': 100, '时段': 120, '状态': 80, '提交时间': 140}, height=8)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load_appts():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM service_appointments WHERE applicant_id=? ORDER BY id DESC",
                                (self.user['id_number'],)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['appt_no'], r['item_name'], r['appt_date'],
                                                r['appt_time'], r['status'], r['created_at']))

        load_appts()

        def cancel_appt():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条预约')
                return
            aid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_appointments WHERE id=?", (aid,)).fetchone()
            conn.close()
            if r['status'] != '已预约':
                messagebox.showinfo('提示', '仅"已预约"状态可取消')
                return
            if not messagebox.askyesno('确认取消', '确定取消该预约吗？'):
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute("UPDATE service_appointments SET status='已取消' WHERE id=?", (aid,))
            conn.commit()
            conn.close()
            DB.log('citizen', self.user['id_number'], self.user['name'], '取消预约', f'取消预约{r["appt_no"]}')
            messagebox.showinfo('成功', '预约已取消')
            load_appts()

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '❌ 取消预约', cancel_appt, COLOR_DANGER, COLOR_DANGER_HOVER, width=12, height=1).pack(side='left', padx=5)

    # ========== 民众：办件进度查询（公开查询） ==========
    def _show_case_query(self):
        self._clear_main()
        make_page_title(self.main_frame, "🔍 办件进度查询").pack(pady=20)
        info_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        info_f.pack(pady=10)
        tk.Label(info_f, text="输入办件编号查询办理进度：", font=("Arial", 12),
                 bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        no_e = make_groove_entry(info_f, width=28)
        no_e.pack(side='left', padx=5)

        def query():
            no = no_e.get().strip()
            if not no:
                messagebox.showwarning('提示', '请输入办件编号')
                return
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE case_no=?", (no,)).fetchone()
            conn.close()
            if not r:
                messagebox.showwarning('提示', f'未查询到办件编号为 {no} 的办件')
                return
            content = f"【申请内容】\n{r['content']}\n\n【办理进度】\n{r['progress'] or '暂无'}\n\n【处理意见】\n{r['handle_comment'] or '暂无'}"
            self._popup_detail(f'办件进度 - {r["case_no"]}',
                               [('办件编号', r['case_no']), ('事项', r['item_name']), ('申请人', r['applicant_name']),
                                ('承办部门', r['department']), ('当前状态', r['status']), ('提交时间', r['created_at'])],
                               '详细信息：', content)

        make_flat_button(info_f, '🔍 查询', query, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10, height=1).pack(side='left', padx=5)

    # ========== 民众：互动交流（咨询/举报/建议） ==========
    def _show_interact_citizen(self):
        self._clear_main()
        make_page_title(self.main_frame, "💬 互动交流").pack(pady=20)
        nb = ttk.Notebook(self.main_frame)
        nb.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        tab1 = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        tab2 = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        tab3 = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        nb.add(tab1, text="💬 政务咨询")
        nb.add(tab2, text="🚨 投诉举报")
        nb.add(tab3, text="💡 意见建议")
        self._build_consult_submit(tab1)
        self._build_report_submit(tab2)
        self._build_suggestion_submit(tab3)

    # 政务咨询（民众提交）
    def _build_consult_submit(self, parent):
        form_f = tk.LabelFrame(parent, text="提交政务咨询", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=30, pady=15)
        form_f.pack(pady=10, padx=50, fill=tk.X)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=5)
        tk.Label(row1, text="咨询分类：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        cat_var = tk.StringVar(value=CONSULT_CATEGORIES[0])
        ttk.Combobox(row1, textvariable=cat_var, values=CONSULT_CATEGORIES, font=("Arial", 11),
                     width=12, state='readonly').pack(side='left', padx=5)
        tk.Label(row1, text="受理部门：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(15, 5))
        dept_var = tk.StringVar(value='国务院')
        ttk.Combobox(row1, textvariable=dept_var, values=DEPARTMENTS, font=("Arial", 11),
                     width=12, state='readonly').pack(side='left', padx=5)
        row2 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row2.pack(fill='x', pady=8)
        tk.Label(row2, text="咨询标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row2, width=45)
        title_e.pack(side='left', padx=5)
        tk.Label(form_f, text="咨询内容：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5, pady=(8, 0))
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 12), height=8, wrap='word')
        content_e.pack(fill='x', padx=5, pady=8)

        def submit():
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写咨询标题和内容')
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO consultations (citizen_id,citizen_name,category,title,content,department,created_at)
                VALUES (?,?,?,?,?,?,?)''', (self.user['id_number'], self.user['name'], cat_var.get(),
                                             title, content, dept_var.get(), now_str()))
            conn.commit()
            conn.close()
            DB.log('citizen', self.user['id_number'], self.user['name'], '提交咨询', f'咨询《{title}》')
            messagebox.showinfo('成功', '咨询提交成功，相关部门将尽快回复')
            title_e.delete(0, 'end')
            content_e.delete('1.0', 'end')

        btn_frame = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        btn_frame.pack(pady=10)
        make_flat_button(btn_frame, "💬 提交咨询", submit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=16).pack()

    # 投诉举报（民众提交）
    def _build_report_submit(self, parent):
        form_f = tk.LabelFrame(parent, text="举报政府官员", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=30, pady=15)
        form_f.pack(pady=10, padx=50, fill=tk.X)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=5)
        tk.Label(row1, text="被举报官员：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        conn = DB.get_conn(GOV_DB)
        officials = conn.execute("SELECT id,name,id_number,department,position FROM officials WHERE status='在职' ORDER BY name").fetchall()
        conn.close()
        off_names = [f"{o['name']}（{o['department']}{o['position']}）" for o in officials]
        off_var = tk.StringVar()
        ttk.Combobox(row1, textvariable=off_var, values=off_names, font=("Arial", 11), width=35, state='readonly').pack(side='left', padx=5)
        row2 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row2.pack(fill='x', pady=8)
        tk.Label(row2, text="举报标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row2, width=45)
        title_e.pack(side='left', padx=5)
        tk.Label(form_f, text="举报内容：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5, pady=(8, 0))
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 12), height=7, wrap='word')
        content_e.pack(fill='x', padx=5, pady=8)
        tk.Label(form_f, text="⚠️ 请如实举报，恶意举报将承担法律责任", font=("Arial", 10),
                 bg=COLOR_CONTENT_BG, fg=COLOR_DANGER).pack(anchor='w', padx=5)

        def submit():
            if not off_var.get():
                messagebox.showwarning('提示', '请选择被举报官员')
                return
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写举报标题和内容')
                return
            idx = off_names.index(off_var.get())
            off = officials[idx]
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO reports (reporter_id,reporter_name,official_id,official_name,title,content,created_at)
                VALUES (?,?,?,?,?,?,?)''', (self.user['id_number'], self.user['name'], off['id_number'],
                                             off['name'], title, content, now_str()))
            conn.commit()
            conn.close()
            DB.log('citizen', self.user['id_number'], self.user['name'], '举报官员', f'举报官员{off["name"]}，标题《{title}》')
            messagebox.showinfo('成功', '举报提交成功，管理员将尽快处理')
            title_e.delete(0, 'end')
            content_e.delete('1.0', 'end')
            off_var.set('')

        btn_frame = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        btn_frame.pack(pady=10)
        make_flat_button(btn_frame, "🚨 提交举报", submit, COLOR_DANGER, COLOR_DANGER_HOVER, width=16).pack()

    # 意见建议（民众提交）
    def _build_suggestion_submit(self, parent):
        form_f = tk.LabelFrame(parent, text="向官员提交建议", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=30, pady=15)
        form_f.pack(pady=10, padx=50, fill=tk.X)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=5)
        tk.Label(row1, text="建议对象：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        conn = DB.get_conn(GOV_DB)
        officials = conn.execute("SELECT id,name,id_number,department,position FROM officials WHERE status='在职' ORDER BY name").fetchall()
        conn.close()
        off_names = [f"{o['name']}（{o['department']}{o['position']}）" for o in officials]
        off_var = tk.StringVar()
        ttk.Combobox(row1, textvariable=off_var, values=off_names, font=("Arial", 11), width=35, state='readonly').pack(side='left', padx=5)
        row2 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row2.pack(fill='x', pady=8)
        tk.Label(row2, text="建议标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row2, width=45)
        title_e.pack(side='left', padx=5)
        tk.Label(form_f, text="建议内容：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5, pady=(8, 0))
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 12), height=7, wrap='word')
        content_e.pack(fill='x', padx=5, pady=8)

        def submit():
            if not off_var.get():
                messagebox.showwarning('提示', '请选择建议对象')
                return
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写建议标题和内容')
                return
            idx = off_names.index(off_var.get())
            off = officials[idx]
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO suggestions (citizen_id,citizen_name,official_id,official_name,title,content,created_at)
                VALUES (?,?,?,?,?,?,?)''', (self.user['id_number'], self.user['name'], off['id_number'],
                                             off['name'], title, content, now_str()))
            conn.commit()
            conn.close()
            DB.log('citizen', self.user['id_number'], self.user['name'], '提交建议', f'向官员{off["name"]}提交建议《{title}》')
            messagebox.showinfo('成功', '建议提交成功，官员将尽快回复')
            title_e.delete(0, 'end')
            content_e.delete('1.0', 'end')
            off_var.set('')

        btn_frame = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        btn_frame.pack(pady=10)
        make_flat_button(btn_frame, "💡 提交建议", submit, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=16).pack()

    # ========== 民众：我的申请（文件审批，含部门选择） ==========
    def _show_my_applications(self):
        self._clear_main()
        make_page_title(self.main_frame, "📝 我的申请").pack(pady=20)
        cid = self.user['id_number']

        form_f = tk.LabelFrame(self.main_frame, text="提交文件审批申请", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=20, pady=10)
        form_f.pack(fill=tk.X, padx=30, pady=5)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=5)
        tk.Label(row1, text="文件标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row1, width=32)
        title_e.pack(side='left', padx=5)
        tk.Label(row1, text="文件类型：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        type_var = tk.StringVar(value='个人申请')
        ttk.Combobox(row1, textvariable=type_var, values=['个人申请', '证明文件', '许可申请', '补贴申请', '其他'],
                      font=("Arial", 11), width=10, state='readonly').pack(side='left')
        tk.Label(row1, text="承办部门：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        dept_var = tk.StringVar(value='国务院')
        ttk.Combobox(row1, textvariable=dept_var, values=DEPARTMENTS, font=("Arial", 11),
                     width=10, state='readonly').pack(side='left')
        tk.Label(form_f, text="文件内容：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5, pady=(8, 0))
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=5, wrap='word')
        content_e.pack(fill='x', padx=5, pady=5)

        def submit():
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写文件标题和内容')
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO approval_files (title,content,file_type,applicant_id,applicant_name,
                applicant_type,target_department,created_at) VALUES (?,?,?,?,?,?,?,?)''',
                (title, content, type_var.get(), self.user['id_number'], self.user['name'], '普通民众',
                 dept_var.get(), now_str()))
            conn.commit()
            conn.close()
            DB.log('citizen', self.user['id_number'], self.user['name'], '提交审批申请', f'提交文件《{title}》')
            messagebox.showinfo('成功', '审批申请提交成功，请等待审批')
            title_e.delete(0, 'end')
            content_e.delete('1.0', 'end')
            load_records()

        make_flat_button(form_f, "📝 提交申请", submit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14).pack(pady=8)

        # 我的申请记录
        tk.Label(self.main_frame, text="📁 我的审批申请记录", font=("Arial", 13, "bold"),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).pack(anchor='w', padx=30, pady=(5, 0))
        f1 = tk.LabelFrame(self.main_frame, text="", bg=COLOR_CONTENT_BG, padx=10, pady=5)
        f1.pack(fill=tk.X, padx=30, pady=5)
        cols = ('id', '标题', '类型', '承办部门', '状态', '审批人', '审批意见', '申请时间')
        t1 = ttk.Treeview(f1, columns=cols, show="headings", height=6)
        for c in cols:
            t1.heading(c, text=c)
            t1.column(c, width=90, anchor=tk.CENTER)
        t1.column('id', width=40)
        t1.column('标题', width=170)
        t1.column('审批意见', width=160)
        t1.column('申请时间', width=130)
        t1.pack(fill=tk.X)

        def load_records():
            for item in t1.get_children():
                t1.delete(item)
            conn = DB.get_conn(GOV_DB)
            for r in conn.execute("SELECT * FROM approval_files WHERE applicant_id=? ORDER BY id DESC", (cid,)).fetchall():
                t1.insert('', 'end', values=(r['id'], r['title'], r['file_type'], r['target_department'] or '未指定',
                                              r['status'], r['approver_name'] or '未审批', r['approve_comment'] or '-', r['created_at']))
            conn.close()

        load_records()

        # 举报记录
        tk.Label(self.main_frame, text="🚨 我的举报记录", font=("Arial", 13, "bold"),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).pack(anchor='w', padx=30, pady=(10, 0))
        f2 = tk.LabelFrame(self.main_frame, text="", bg=COLOR_CONTENT_BG, padx=10, pady=5)
        f2.pack(fill=tk.X, padx=30, pady=5)
        cols2 = ('id', '标题', '被举报官员', '状态', '处理结果', '举报时间')
        t2 = ttk.Treeview(f2, columns=cols2, show="headings", height=5)
        for c in cols2:
            t2.heading(c, text=c)
            t2.column(c, width=100, anchor=tk.CENTER)
        t2.column('id', width=40)
        t2.column('标题', width=180)
        t2.column('处理结果', width=200)
        t2.column('举报时间', width=130)
        t2.pack(fill=tk.X)
        conn = DB.get_conn(GOV_DB)
        for r in conn.execute("SELECT * FROM reports WHERE reporter_id=? ORDER BY id DESC", (cid,)).fetchall():
            t2.insert('', 'end', values=(r['id'], r['title'], r['official_name'], r['status'],
                                          r['handle_result'] or '-', r['created_at']))

        # 建议记录
        tk.Label(self.main_frame, text="💡 我的建议记录", font=("Arial", 13, "bold"),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).pack(anchor='w', padx=30, pady=(10, 0))
        f3 = tk.LabelFrame(self.main_frame, text="", bg=COLOR_CONTENT_BG, padx=10, pady=5)
        f3.pack(fill=tk.X, padx=30, pady=5)
        cols3 = ('id', '标题', '建议对象', '状态', '官员回复', '提交时间')
        t3 = ttk.Treeview(f3, columns=cols3, show="headings", height=5)
        for c in cols3:
            t3.heading(c, text=c)
            t3.column(c, width=100, anchor=tk.CENTER)
        t3.column('id', width=40)
        t3.column('标题', width=180)
        t3.column('官员回复', width=200)
        t3.column('提交时间', width=130)
        t3.pack(fill=tk.X)
        for r in conn.execute("SELECT * FROM suggestions WHERE citizen_id=? ORDER BY id DESC", (cid,)).fetchall():
            t3.insert('', 'end', values=(r['id'], r['title'], r['official_name'], r['status'],
                                          r['reply'] or '-', r['created_at']))
        conn.close()

    # ========== 通用：政务公开（公告 + 政策文件库） ==========
    def _show_announcements_view(self):
        self._clear_main()
        make_page_title(self.main_frame, "📢 政务公开").pack(pady=20)
        nb = ttk.Notebook(self.main_frame)
        nb.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        tab1 = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        tab2 = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        tab3 = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        tab4 = tk.Frame(nb, bg='white')
        nb.add(tab1, text="📢 通知公告")
        nb.add(tab2, text="📜 政策文件库")
        nb.add(tab3, text="📜 总统签署的行政令")
        tab5 = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        nb.add(tab4, text="🖼 领导人肖像")
        nb.add(tab5, text="📜 蛋国建国文件查看")
        self._build_announcements_list(tab1)
        self._build_policy_view(tab2)
        self._build_exec_order_view(tab3)
        self._build_leader_portraits(tab4)
        self._render_founding_docs(tab5)

    def _build_leader_portraits(self, parent):
        """政务公开 · 领导人肖像：仿 Apple Leadership 版式（白底、大图居中、姓名 + 职务网格）"""
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute(
            "SELECT * FROM officials WHERE position IN ('总统','副总统') AND status='在职' "
            "ORDER BY CASE position WHEN '总统' THEN 0 ELSE 1 END, id").fetchall()
        conn.close()
        _pres = ''
        for _x in rows:
            if _x['position'] == '总统':
                _pres = _x['name'] or ''
                break
        _subtitle = f"{_pres}政府" if _pres else "蛋国联邦政府"

        outer = tk.Frame(parent, bg='white')
        outer.pack(fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(outer, orient='vertical')
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas = tk.Canvas(outer, bg='white', highlightthickness=0, yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=canvas.yview)
        inner = tk.Frame(canvas, bg='white')
        _win_id = canvas.create_window((0, 0), window=inner, anchor='nw')

        def _on_conf(_e=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
            canvas.itemconfigure(_win_id, width=canvas.winfo_width())
        inner.bind('<Configure>', _on_conf)
        canvas.bind('<Configure>', _on_conf)

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')
        canvas.bind('<MouseWheel>', _wheel)
        inner.bind('<MouseWheel>', _wheel)

        tk.Label(inner, text="总统与副总统肖像", font=("Arial", 32, "bold"),
                 bg='white', fg='#1d1d1f').pack(pady=(40, 8))
        tk.Label(inner, text=_subtitle, font=("Arial", 14),
                 bg='white', fg='#6e6e73').pack()
        tk.Frame(inner, bg='#d2d2d7', height=1).pack(fill='x', padx=120, pady=30)

        grid = tk.Frame(inner, bg='white')
        grid.pack(pady=(0, 20))
        photos = []
        for idx, r in enumerate(rows):
            card = tk.Frame(grid, bg='white')
            card.grid(row=idx // 2, column=idx % 2, padx=34, pady=22)
            try:
                _pp = r['photo'] or ''
            except Exception:
                _pp = ''
            ph, has = make_portrait_photo(_pp, 230, True, 'white')
            if ph:
                photos.append(ph)
                lb = tk.Label(card, image=ph, bg='white', bd=0, cursor='hand2')
                lb.image = ph
                lb.pack()
                lb.bind('<Button-1>',
                        lambda _e, rr=dict(r): self._portrait_viewer(rr))
            else:
                _ph_holder = tk.Label(card, text='👤', font=("Arial", 92),
                                      bg='white', fg='#c7c7cc', cursor='hand2')
                _ph_holder.pack(pady=(60, 12))
                _ph_holder.bind('<Button-1>',
                                lambda _e, rr=dict(r): self._portrait_viewer(rr))
            tk.Label(card, text=r['name'], font=("Arial", 22, "bold"),
                     bg='white', fg='#1d1d1f').pack(pady=(16, 4))
            tk.Label(card, text=r['position'] or '', font=("Arial", 12),
                     bg='white', fg='#6e6e73').pack()
            _pty, _intro = '', ''
            try:
                _pty = r['party'] or ''
                _intro = r['intro'] or ''
            except Exception:
                pass
            if _pty:
                tk.Label(card, text=f"所属党派：{_pty}", font=("Arial", 10),
                         bg='white', fg='#86868b').pack(pady=(3, 0))
            if _intro:
                tk.Label(card, text=_intro, font=("Arial", 10), bg='white',
                         fg='#6e6e73', wraplength=258, justify='center').pack(pady=(10, 0))
            if not has:
                tk.Label(card, text="暂未上传肖像", font=("Arial", 9),
                         bg='white', fg='#9a9a9f').pack(pady=(6, 0))
        if not rows:
            tk.Label(inner, text="暂无总统 / 副总统记录", font=("Arial", 14),
                     bg='white', fg='#9a9a9f').pack(pady=80)
        self._leader_photos = photos  # 保持引用，防止图片被回收
        tk.Label(inner, text="💡 肖像、介绍与所属党派由官员本人在「我的信息」中上传与维护",
                 font=("Arial", 10), bg='white', fg='#9a9a9f').pack(pady=(10, 40))

    def _build_announcements_list(self, parent):
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM announcements ORDER BY id DESC").fetchall()
        conn.close()
        if not rows:
            tk.Label(parent, text="暂无公告", font=("Arial", 16), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=50)
            return
        list_frame = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        sb = tk.Scrollbar(list_frame)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas = tk.Canvas(list_frame, yscrollcommand=sb.set, bg=COLOR_CONTENT_BG, highlightthickness=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=canvas.yview)
        inner = tk.Frame(canvas, bg=COLOR_CONTENT_BG)
        canvas.create_window((0, 0), window=inner, anchor='nw')

        def on_configure(event):
            canvas.configure(scrollregion=canvas.bbox('all'))
        inner.bind('<Configure>', on_configure)
        canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units'))

        for r in rows:
            card = tk.Frame(inner, bg='#f8f9fa', bd=1, relief='solid')
            card.pack(fill='x', padx=15, pady=5)
            header = tk.Frame(card, bg='#f8f9fa')
            header.pack(fill='x', padx=15, pady=(10, 0))
            pri_color = {'紧急': COLOR_DANGER, '重要': COLOR_WARNING, '普通': COLOR_PRIMARY}.get(r['priority'], COLOR_PRIMARY)
            tk.Label(header, text=f"[{r['priority']}]", font=("Arial", 11, "bold"), bg='#f8f9fa', fg=pri_color).pack(side='left')
            tk.Label(header, text=r['title'], font=("Arial", 14, "bold"), bg='#f8f9fa', fg=COLOR_TITLE).pack(side='left', padx=8)
            tk.Label(header, text=f"{r['publisher']} | {r['created_at']}", font=("Arial", 10),
                     bg='#f8f9fa', fg='#999').pack(side='right')
            tk.Label(card, text=r['content'], font=("Arial", 11), bg='#f8f9fa', fg='#333',
                     wraplength=1000, justify='left').pack(anchor='w', padx=15, pady=10)

    def _build_policy_view(self, parent):
        filter_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        filter_f.pack(pady=5)
        tk.Label(filter_f, text="关键词：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        kw_e = make_groove_entry(filter_f, width=22)
        kw_e.pack(side='left', padx=5)

        list_f = tk.LabelFrame(parent, text="政策文件列表", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        cols = ('id', '文号', '标题', '分类', '发文机关', '发布日期')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '文号': 160, '标题': 300, '分类': 100,
                                            '发文机关': 100, '发布日期': 110}, height=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            kw = kw_e.get().strip()
            if kw:
                k = f'%{kw}%'
                rows = conn.execute("SELECT * FROM policy_docs WHERE is_active=1 AND (title LIKE ? OR doc_no LIKE ? OR issuing_department LIKE ?) ORDER BY issue_date DESC",
                                    (k, k, k)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM policy_docs WHERE is_active=1 ORDER BY issue_date DESC").fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['doc_no'] or '-', r['title'], r['category'] or '-',
                                                r['issuing_department'] or '-', r['issue_date'] or '-'))

        load()
        tk.Button(filter_f, text="查询", command=load, width=8, bg=COLOR_PRIMARY, fg='white',
                  relief=tk.FLAT, cursor='hand2').pack(side='left', padx=5)

        def view_doc():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一份政策文件')
                return
            did = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM policy_docs WHERE id=?", (did,)).fetchone()
            conn.close()
            self._popup_detail(f'政策文件 - {r["title"]}',
                               [('文号', r['doc_no'] or '-'), ('标题', r['title']), ('分类', r['category'] or '-'),
                                ('发文机关', r['issuing_department'] or '-'), ('发布日期', r['issue_date'] or '-'), ('发布人', r['publisher'])],
                               '文件全文：', r['content'])

        btn_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=8)
        make_flat_button(btn_f, '📄 查看全文', view_doc, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12, height=1).pack(side='left', padx=5)

    def _build_exec_order_view(self, parent):
        """政务公开：总统签署的行政令（平民/官员/管理员均可见，双击查看详情）。"""
        filter_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        filter_f.pack(pady=5)
        tk.Label(filter_f, text="关键词：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        kw_e = make_groove_entry(filter_f, width=22)
        kw_e.pack(side='left', padx=5)

        list_f = tk.LabelFrame(parent, text="总统签署的行政令", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        cols = ('id', '编号', '标题', '发布人', '生效方式', '生效时间', '状态', '发布时间')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '编号': 140, '标题': 260, '发布人': 100,
                                                    '生效方式': 90, '生效时间': 140, '状态': 80,
                                                    '发布时间': 140}, height=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        tree.tag_configure('已生效', foreground='#2e7d32')
        tree.tag_configure('待生效', foreground='#1565c0')
        tree.tag_configure('已撤回', foreground='#c62828')

        def load():
            for item in tree.get_children():
                tree.delete(item)
            kw = kw_e.get().strip()
            for r in DB.list_executive_orders():
                if kw and kw not in r['title'] and kw not in r['order_no'] and kw not in r['content']:
                    continue
                tree.insert('', 'end', values=(r['id'], r['order_no'], r['title'],
                                               f"{r['publisher_name']}（{r['publisher_position']}）",
                                               r['effective_mode'], r['effective_time'],
                                               r['status'], r['created_at']),
                            tags=(r['status'],))

        load()
        tk.Button(filter_f, text="查询", command=load, width=8, bg=COLOR_PRIMARY, fg='white',
                  relief=tk.FLAT, cursor='hand2').pack(side='left', padx=5)

        def view_detail():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条行政令')
                return
            r = DB.get_executive_order(int(tree.item(sel[0])['values'][0]))
            if not r:
                messagebox.showwarning('提示', '行政令不存在')
                return
            info = [('编号', r['order_no']), ('标题', r['title']),
                    ('发布人', f"{r['publisher_name']}（{r['publisher_position']}）"),
                    ('生效方式', r['effective_mode']), ('生效时间', r['effective_time']),
                    ('当前状态', r['status']), ('发布时间', r['created_at'])]
            if r['is_withdrawn']:
                info.append(('撤回时间', r['withdrawn_at'] or '-'))
                info.append(('撤回人', r['withdrawn_by'] or '-'))
            self._popup_detail(f'行政令详情 - {r["order_no"]}', info, '行政令全文：', r['content'])

        btn_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=8)
        make_flat_button(btn_f, '📄 查看详情', view_detail, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=12, height=1).pack(side='left', padx=5)
        tree.bind('<Double-1>', lambda e: view_detail())

    # ========== 官员：办件办理（按部门） ==========
    def _show_cases_official(self):
        if not self._guard('handle', '办件办理'):
            return
        self._clear_main()
        make_page_title(self.main_frame, "🗂️ 办件办理（本部门）").pack(pady=20)
        dept = self.user['department']

        filter_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        filter_f.pack(pady=5)
        tk.Label(filter_f, text="状态：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        st_var = tk.StringVar(value='全部')
        ttk.Combobox(filter_f, textvariable=st_var, values=['全部'] + CASE_STATUS,
                      font=("Arial", 11), width=10, state='readonly').pack(side='left', padx=5)

        list_frame = tk.LabelFrame(self.main_frame, text="本部门办件列表", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '办件编号', '事项名称', '申请人', '当前状态', '办理人', '提交时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '办件编号': 150, '事项名称': 200,
                                                '申请人': 100, '当前状态': 90, '办理人': 90, '提交时间': 130}, height=13)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            st = st_var.get()
            if st == '全部':
                rows = conn.execute("SELECT * FROM service_cases WHERE department=? ORDER BY id DESC", (dept,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM service_cases WHERE department=? AND status=? ORDER BY id DESC", (dept, st)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['case_no'], r['item_name'], r['applicant_name'],
                                                r['status'], r['handler_name'] or '-', r['created_at']))

        load()
        st_var.trace_add('write', lambda *a: load())

        def handle_case():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条办件')
                return
            sid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE id=?", (sid,)).fetchone()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'办件办理 - {r["case_no"]}')
            win.geometry('680x620')
            win.transient(self.root)
            win.grab_set()
            info_f = tk.Frame(win, bg='white')
            info_f.pack(fill='x', padx=15, pady=10)
            info = [('办件编号', r['case_no']), ('事项', r['item_name']), ('申请人', r['applicant_name']),
                    ('联系电话', r['applicant_phone'] or '-'), ('当前状态', r['status']), ('提交时间', r['created_at'])]
            for i, (k, v) in enumerate(info):
                tk.Label(info_f, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e', bg='white').grid(
                    row=i // 3, column=(i % 3) * 2, sticky='e', padx=5, pady=3)
                tk.Label(info_f, text=str(v), font=("Arial", 10), anchor='w', bg='white').grid(
                    row=i // 3, column=(i % 3) * 2 + 1, sticky='w', pady=3)
            # 无犯罪记录证明 → 对接户籍系统核查
            cr_frame = None
            if r['item_code'] == 'ZJ-001':
                cr_frame = tk.LabelFrame(win, text="公安系统犯罪记录核验（自动对接户籍系统）",
                                         font=("Arial", 10, "bold"), padx=8, pady=5)
                cr_frame.pack(fill='x', padx=15, pady=5)
                crs = DB.get_criminal_records(r['applicant_id'])
                if crs is None:
                    tk.Label(cr_frame, text="⚠️ 无法连接户籍系统核查", fg=COLOR_DANGER).pack(anchor='w')
                elif not crs:
                    tk.Label(cr_frame, text="✅ 未查询到犯罪记录", fg=COLOR_SUCCESS).pack(anchor='w')
                else:
                    tk.Label(cr_frame, text=f"⚠️ 发现 {len(crs)} 条犯罪记录：", fg=COLOR_DANGER).pack(anchor='w')
                    for c in crs:
                        tk.Label(cr_frame, text=f"  · {c.get('case_name','')}（{c.get('case_date','')}）处罚：{c.get('punishment','')}",
                                 font=("Arial", 9), anchor='w', wraplength=600).pack(anchor='w')

            tk.Label(win, text='申请内容：', font=("Arial", 11, "bold")).pack(anchor='w', padx=15)
            ct = scrolledtext.ScrolledText(win, font=("Arial", 11), height=5, wrap='word')
            ct.pack(fill='x', padx=15, pady=5)
            ct.insert('1.0', r['content'])
            ct.config(state='disabled')

            tk.Label(win, text='办理进度记录：', font=("Arial", 11, "bold")).pack(anchor='w', padx=15)
            prog_txt = scrolledtext.ScrolledText(win, font=("Arial", 11), height=5, wrap='word')
            prog_txt.pack(fill='x', padx=15, pady=5)
            prog_txt.insert('1.0', r['progress'] or '')
            prog_txt.config(state='disabled')

            tk.Label(win, text='处理意见（退回理由等）：', font=("Arial", 11, "bold")).pack(anchor='w', padx=15)
            cmt_e = scrolledtext.ScrolledText(win, font=("Arial", 11), height=3, wrap='word')
            cmt_e.pack(fill='x', padx=15, pady=5)
            if r['handle_comment']:
                cmt_e.insert('1.0', r['handle_comment'])

            def do_status(new_status, need_comment=False, need_progress=False):
                comment = cmt_e.get('1.0', 'end').strip()
                if need_comment and not comment:
                    messagebox.showwarning('提示', '请填写处理意见')
                    return
                prog_now = f"{now_str()} 【{self.user['name']}】" + (f" {comment}" if comment else '')
                if need_progress and not comment:
                    messagebox.showwarning('提示', '请填写办理进度说明')
                    return
                conn = DB.get_conn(GOV_DB)
                if new_status == '已办结':
                    conn.execute('''UPDATE service_cases SET status=?, handler_id=?, handler_name=?, handle_comment=?,
                        progress=progress || char(10) || ?, finished_at=?, rating=0 WHERE id=?''',
                        (new_status, self.user['id_number'], self.user['name'], comment, prog_now, now_str(), sid))
                else:
                    conn.execute('''UPDATE service_cases SET status=?, handler_id=?, handler_name=?, handle_comment=?,
                        progress=progress || char(10) || ? WHERE id=?''',
                        (new_status, self.user['id_number'], self.user['name'], comment, prog_now, sid))
                conn.commit()
                conn.close()
                DB.log('official', self.user['id_number'], self.user['name'], '办理办件',
                       f'办件{r["case_no"]}状态变更为{new_status}')
                DB.send_message('citizen', r['applicant_id'], '办件进度通知',
                                f'您的办件 {r["case_no"]}（{r["item_name"]}）状态已更新为：{new_status}。\n{comment}')
                messagebox.showinfo('成功', f'办件已{new_status}')
                win.destroy()
                load()

            btn_f = tk.Frame(win)
            btn_f.pack(fill='x', padx=15, pady=10)
            if r['status'] == '待受理':
                make_flat_button(btn_f, '✅ 受理', lambda: do_status('受理中'), COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10, height=1).pack(side='left', padx=6)
                make_flat_button(btn_f, '↩️ 退回', lambda: do_status('已退回', need_comment=True), COLOR_DANGER, COLOR_DANGER_HOVER, width=10, height=1).pack(side='left', padx=6)
            if r['status'] == '受理中':
                make_flat_button(btn_f, '⏩ 转入办理', lambda: do_status('办理中', need_comment=True), COLOR_WARNING, COLOR_WARNING, width=12, height=1).pack(side='left', padx=6)
                make_flat_button(btn_f, '↩️ 退回', lambda: do_status('已退回', need_comment=True), COLOR_DANGER, COLOR_DANGER_HOVER, width=10, height=1).pack(side='left', padx=6)
            if r['status'] == '办理中':
                make_flat_button(btn_f, '🏁 办结', lambda: do_status('已办结', need_comment=True), COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=10, height=1).pack(side='left', padx=6)
                make_flat_button(btn_f, '↩️ 退回', lambda: do_status('已退回', need_comment=True), COLOR_DANGER, COLOR_DANGER_HOVER, width=10, height=1).pack(side='left', padx=6)

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '⚙️ 办理办件', handle_case, COLOR_WARNING, COLOR_WARNING, width=12, height=1).pack(side='left', padx=5)

    # ========== 官员：公文管理（收发文） ==========
    def _show_docs_official(self):
        self._clear_main()
        make_page_title(self.main_frame, "📄 公文管理（收发文）").pack(pady=20)
        dept = self.user['department']

        form_f = tk.LabelFrame(self.main_frame, text="发文", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=20, pady=10)
        can_send_doc = self._can('send_doc')
        if not can_send_doc:
            tk.Label(self.main_frame,
                     text="⛔ 您当前没有“公文发文”权限，可在下方查看本部门收发文；如需发文请联系管理员开通。",
                     font=("Arial", 11, 'bold'), bg='#fff4f4', fg=COLOR_DANGER,
                     padx=12, pady=8).pack(fill='x', padx=30, pady=(8, 2))
        form_f.pack(fill=tk.X, padx=30, pady=5) if can_send_doc else None
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=5)
        tk.Label(row1, text="标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row1, width=34)
        title_e.pack(side='left', padx=5)
        tk.Label(row1, text="发往部门：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        to_var = tk.StringVar(value=DEPARTMENTS[0])
        ttk.Combobox(row1, textvariable=to_var, values=DB.active_departments(), font=("Arial", 11),
                     width=10, state='readonly').pack(side='left')
        tk.Label(row1, text="紧急程度：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        urg_var = tk.StringVar(value='普通')
        ttk.Combobox(row1, textvariable=urg_var, values=DOC_URGENCY, font=("Arial", 11),
                     width=8, state='readonly').pack(side='left')
        tk.Label(form_f, text="公文内容：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5, pady=(5, 0))
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=4, wrap='word')
        content_e.pack(fill='x', padx=5, pady=5)

        def send_doc():
            if not self._guard('send_doc', '公文发文'):
                return
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写公文标题和内容')
                return
            doc_no = gen_no('GW')
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO official_docs (doc_no,title,doc_type,urgency,from_department,to_department,
                sender_id,sender_name,content,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (doc_no, title, '发文', urg_var.get(), dept, to_var.get(),
                 self.user['id_number'], self.user['name'], content, '待签收', now_str()))
            conn.commit()
            conn.close()
            DB.log('official', self.user['id_number'], self.user['name'], '发文', f'发文《{title}》至{to_var.get()}')
            messagebox.showinfo('成功', f'公文已发出！文号：{doc_no}')
            title_e.delete(0, 'end')
            content_e.delete('1.0', 'end')
            load_docs()

        make_flat_button(form_f, '📤 发出公文', send_doc, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14).pack(pady=8)

        list_f = tk.LabelFrame(self.main_frame, text="本部门公文（收文/发文）", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '文号', '标题', '类型', '紧急', '发往/来自', '发送人', '状态', '时间')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '文号': 150, '标题': 220, '类型': 60, '紧急': 60,
                                            '发往/来自': 100, '发送人': 80, '状态': 80, '时间': 130}, height=9)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load_docs():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute(
                "SELECT * FROM official_docs WHERE from_department=? OR to_department=? ORDER BY id DESC",
                (dept, dept)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['doc_no'], r['title'], r['doc_type'], r['urgency'],
                                                r['to_department'] or r['from_department'], r['sender_name'],
                                                r['status'], r['created_at']))

        load_docs()

        def handle_doc():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一份公文')
                return
            did = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM official_docs WHERE id=?", (did,)).fetchone()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'公文详情 - {r["doc_no"]}')
            win.geometry('620x560')
            win.transient(self.root)
            win.grab_set()
            info_f = tk.Frame(win, bg='white')
            info_f.pack(fill='x', padx=15, pady=10)
            info = [('文号', r['doc_no']), ('标题', r['title']), ('类型', r['doc_type']),
                    ('紧急程度', r['urgency']), ('发文部门', r['from_department']), ('收文部门', r['to_department'] or '-'),
                    ('发送人', r['sender_name']), ('状态', r['status']), ('时间', r['created_at'])]
            for i, (k, v) in enumerate(info):
                tk.Label(info_f, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e', bg='white').grid(
                    row=i // 3, column=(i % 3) * 2, sticky='e', padx=5, pady=3)
                tk.Label(info_f, text=str(v), font=("Arial", 10), anchor='w', bg='white').grid(
                    row=i // 3, column=(i % 3) * 2 + 1, sticky='w', pady=3)
            tk.Label(win, text='公文内容：', font=("Arial", 11, "bold")).pack(anchor='w', padx=15)
            ct = scrolledtext.ScrolledText(win, font=("Arial", 11), height=8, wrap='word')
            ct.pack(fill='both', expand=True, padx=15, pady=5)
            ct.insert('1.0', r['content'])
            ct.config(state='disabled')
            tk.Label(win, text='办理意见：', font=("Arial", 11, "bold")).pack(anchor='w', padx=15)
            cmt_e = scrolledtext.ScrolledText(win, font=("Arial", 11), height=3, wrap='word')
            cmt_e.pack(fill='x', padx=15, pady=5)
            if r['handle_comment']:
                cmt_e.insert('1.0', r['handle_comment'])

            def do_doc(new_status):
                comment = cmt_e.get('1.0', 'end').strip()
                conn = DB.get_conn(GOV_DB)
                conn.execute("UPDATE official_docs SET status=?, handler_name=?, handle_comment=?, handled_at=? WHERE id=?",
                             (new_status, self.user['name'], comment, now_str(), did))
                conn.commit()
                conn.close()
                DB.log('official', self.user['id_number'], self.user['name'], '办理公文',
                       f'公文{r["doc_no"]}状态变更为{new_status}')
                messagebox.showinfo('成功', f'公文已{new_status}')
                win.destroy()
                load_docs()

            btn_f = tk.Frame(win)
            btn_f.pack(fill='x', padx=15, pady=10)
            if r['status'] == '待签收' and r['to_department'] == dept:
                make_flat_button(btn_f, '✅ 签收', lambda: do_doc('已签收'), COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10, height=1).pack(side='left', padx=6)
            if r['status'] == '已签收':
                make_flat_button(btn_f, '📝 办理', lambda: do_doc('已办理'), COLOR_WARNING, COLOR_WARNING, width=10, height=1).pack(side='left', padx=6)
            if r['status'] in ('已签收', '已办理'):
                make_flat_button(btn_f, '🏁 办结', lambda: do_doc('已办结'), COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=10, height=1).pack(side='left', padx=6)

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '📄 查看/办理', handle_doc, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12, height=1).pack(side='left', padx=5)

    # ========== 官员：互动办理（建议回复 + 咨询回复） ==========
    def _show_interact_official(self):
        if not self._guard('handle', '互动办理（回复咨询/举报）'):
            return
        self._clear_main()
        make_page_title(self.main_frame, "💬 互动办理").pack(pady=20)
        nb = ttk.Notebook(self.main_frame)
        nb.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        tab1 = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        tab2 = tk.Frame(nb, bg=COLOR_CONTENT_BG)
        nb.add(tab1, text="💬 群众建议")
        nb.add(tab2, text="💡 咨询回复")
        self._build_suggestions_reply(tab1)
        self._build_consult_reply(tab2)

    def _build_suggestions_reply(self, parent):
        oid = self.user['id_number']
        list_frame = tk.LabelFrame(parent, text="收到的建议", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        cols = ('id', '标题', '建议人', '状态', '回复人', '提交时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '标题': 260, '建议人': 100, '状态': 90,
                                                '回复人': 90, '提交时间': 140}, height=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM suggestions WHERE official_id=? ORDER BY id DESC", (oid,)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], r['citizen_name'], r['status'],
                                                r['replier'] or '未回复', r['created_at']))

        load()

        def reply_suggestion():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条建议')
                return
            sid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM suggestions WHERE id=?", (sid,)).fetchone()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'建议详情 - {r["title"]}')
            win.geometry('550x500')
            win.transient(self.root)
            win.grab_set()
            info = [('建议标题', r['title']), ('建议人', r['citizen_name']), ('状态', r['status']), ('提交时间', r['created_at'])]
            for i, (k, v) in enumerate(info):
                tk.Label(win, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e').grid(row=i, column=0, sticky='e', padx=10, pady=4)
                tk.Label(win, text=str(v), font=("Arial", 10), anchor='w').grid(row=i, column=1, sticky='w', pady=4)
            tk.Label(win, text='建议内容：', font=("Arial", 11, "bold")).grid(row=4, column=0, sticky='nw', padx=10, pady=5)
            ct = scrolledtext.ScrolledText(win, font=("Arial", 11), height=7, wrap='word', width=50)
            ct.grid(row=4, column=1, pady=5, padx=5)
            ct.insert('1.0', r['content'])
            ct.config(state='disabled')
            tk.Label(win, text='我的回复：', font=("Arial", 11, "bold")).grid(row=5, column=0, sticky='nw', padx=10, pady=5)
            reply_e = scrolledtext.ScrolledText(win, font=("Arial", 11), height=5, wrap='word', width=50)
            reply_e.grid(row=5, column=1, pady=5, padx=5)
            if r['reply']:
                reply_e.insert('1.0', r['reply'])

            def do_reply():
                reply_text = reply_e.get('1.0', 'end').strip()
                if not reply_text:
                    messagebox.showwarning('提示', '请填写回复内容')
                    return
                conn = DB.get_conn(GOV_DB)
                conn.execute('''UPDATE suggestions SET status='已回复', reply=?, replier=?, replied_at=? WHERE id=?''',
                             (reply_text, self.user['name'], now_str(), sid))
                conn.commit()
                conn.close()
                DB.log('official', self.user['id'], self.user['name'], '回复建议', f'回复建议《{r["title"]}》')
                DB.send_message('citizen', r['citizen_id'], '建议回复通知',
                                f'您的建议《{r["title"]}》已获回复。\n{reply_text}')
                messagebox.showinfo('成功', '回复已提交')
                win.destroy()
                load()

            make_flat_button(win, '✅ 提交回复', do_reply, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=14).grid(row=6, column=0, columnspan=2, pady=15)

        btn_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '💬 查看/回复', reply_suggestion, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=14, height=1).pack(side='left', padx=5)

    def _build_consult_reply(self, parent):
        dept = self.user['department']
        list_frame = tk.LabelFrame(parent, text="本部门收到的咨询", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        cols = ('id', '标题', '咨询人', '分类', '状态', '提交时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '标题': 260, '咨询人': 100, '分类': 90,
                                                '状态': 90, '提交时间': 140}, height=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM consultations WHERE department=? ORDER BY id DESC", (dept,)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], r['citizen_name'], r['category'],
                                                r['status'], r['created_at']))

        load()

        def reply_consult():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条咨询')
                return
            sid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM consultations WHERE id=?", (sid,)).fetchone()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'咨询详情 - {r["title"]}')
            win.geometry('560x500')
            win.transient(self.root)
            win.grab_set()
            info = [('咨询标题', r['title']), ('咨询人', r['citizen_name']), ('分类', r['category']),
                    ('状态', r['status']), ('提交时间', r['created_at'])]
            for i, (k, v) in enumerate(info):
                tk.Label(win, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e').grid(row=i, column=0, sticky='e', padx=10, pady=4)
                tk.Label(win, text=str(v), font=("Arial", 10), anchor='w').grid(row=i, column=1, sticky='w', pady=4)
            tk.Label(win, text='咨询内容：', font=("Arial", 11, "bold")).grid(row=5, column=0, sticky='nw', padx=10, pady=5)
            ct = scrolledtext.ScrolledText(win, font=("Arial", 11), height=7, wrap='word', width=50)
            ct.grid(row=5, column=1, pady=5, padx=5)
            ct.insert('1.0', r['content'])
            ct.config(state='disabled')
            tk.Label(win, text='回复内容：', font=("Arial", 11, "bold")).grid(row=6, column=0, sticky='nw', padx=10, pady=5)
            reply_e = scrolledtext.ScrolledText(win, font=("Arial", 11), height=5, wrap='word', width=50)
            reply_e.grid(row=6, column=1, pady=5, padx=5)
            if r['reply']:
                reply_e.insert('1.0', r['reply'])

            def do_reply():
                reply_text = reply_e.get('1.0', 'end').strip()
                if not reply_text:
                    messagebox.showwarning('提示', '请填写回复内容')
                    return
                conn = DB.get_conn(GOV_DB)
                conn.execute('''UPDATE consultations SET status='已回复', reply=?, replier=?, replied_at=? WHERE id=?''',
                             (reply_text, self.user['name'], now_str(), sid))
                conn.commit()
                conn.close()
                DB.log('official', self.user['id'], self.user['name'], '回复咨询', f'回复咨询《{r["title"]}》')
                DB.send_message('citizen', r['citizen_id'], '咨询回复通知',
                                f'您的咨询《{r["title"]}》已获回复。\n{reply_text}')
                messagebox.showinfo('成功', '回复已提交')
                win.destroy()
                load()

            make_flat_button(win, '✅ 提交回复', do_reply, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=14).grid(row=7, column=0, columnspan=2, pady=15)

        btn_f = tk.Frame(parent, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '💬 查看/回复', reply_consult, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=14, height=1).pack(side='left', padx=5)

    # ========== 官员：工作报告 ==========
    def _show_work_report(self):
        if not self._guard('handle', '提交工作报告'):
            return
        self._clear_main()
        make_page_title(self.main_frame, "📄 工作报告").pack(pady=20)
        oid = self.user['id_number']

        form_f = tk.LabelFrame(self.main_frame, text="提交工作报告", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=20, pady=10)
        form_f.pack(fill=tk.X, padx=30, pady=5)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=5)
        tk.Label(row1, text="报告标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row1, width=45)
        title_e.pack(side='left', padx=5)
        tk.Label(form_f, text="报告内容：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5, pady=(5, 0))
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=6, wrap='word')
        content_e.pack(fill='x', padx=5, pady=5)

        def submit():
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写报告标题和内容')
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO work_reports (official_id,official_name,title,content,created_at)
                VALUES (?,?,?,?,?)''', (oid, self.user['name'], title, content, now_str()))
            conn.commit()
            conn.close()
            DB.log('official', self.user['id'], self.user['name'], '提交工作报告', f'提交报告《{title}》')
            messagebox.showinfo('成功', '工作报告提交成功')
            title_e.delete(0, 'end')
            content_e.delete('1.0', 'end')
            load_reports()

        make_flat_button(form_f, '📄 提交报告', submit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14).pack(pady=8)

        list_f = tk.LabelFrame(self.main_frame, text="历史报告", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '标题', '状态', '审阅人', '提交时间')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '标题': 320, '状态': 90, '审阅人': 90, '提交时间': 150}, height=8)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load_reports():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM work_reports WHERE official_id=? ORDER BY id DESC", (oid,)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], r['status'], r['reviewer'] or '未审阅', r['created_at']))

        load_reports()

    # ========== 官员：我的信息 ==========
    def _show_my_info(self):
        self._clear_main()
        make_page_title(self.main_frame, "👤 我的信息").pack(pady=20)

        conn = DB.get_conn(GOV_DB)
        r = conn.execute("SELECT * FROM officials WHERE id_number=?", (self.user['id_number'],)).fetchone()
        conn.close()
        if not r:
            tk.Label(self.main_frame, text="未查询到任职信息", font=("Arial", 16),
                     bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=50)
            return

        info_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        info_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)

        avatar_f = tk.Frame(info_f, bg=COLOR_CONTENT_BG)
        avatar_f.pack(fill=tk.X, pady=10)
        _photo_path = ''
        try:
            _photo_path = r['photo'] or ''
        except Exception:
            _photo_path = ''
        _photo, _has_real = make_portrait_photo(_photo_path, 140, True, COLOR_CONTENT_BG)
        if _photo:
            _p_lbl = tk.Label(avatar_f, image=_photo, bg=COLOR_CONTENT_BG, bd=0)
            _p_lbl.image = _photo
            _p_lbl.pack()
        else:
            tk.Label(avatar_f, text='👤', font=("Arial", 60), bg=COLOR_CONTENT_BG).pack()
        _p_btn_row = tk.Frame(avatar_f, bg=COLOR_CONTENT_BG)
        _p_btn_row.pack(pady=(8, 0))
        make_flat_button(_p_btn_row, '📷 更换肖像' if _has_real else '📷 上传肖像',
                         lambda: self._upload_my_portrait(r['id']),
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14, height=1).pack(side='left')
        if _photo_path:
            make_flat_button(_p_btn_row, '🗑 删除肖像', lambda: self._delete_my_portrait(r['id']),
                             '#8e8e93', '#6c6c70', width=12, height=1).pack(side='left', padx=8)
        tk.Label(avatar_f, text=r['name'], font=("Arial", 20, "bold"), bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).pack(pady=(8, 0))
        tk.Label(avatar_f, text=f"{r['department']} · {r['position']} · {r['rank']}", font=("Arial", 13),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=5)
        status_color = COLOR_SUCCESS if r['status'] == '在职' else COLOR_DANGER
        tk.Label(avatar_f, text=f"状态：{r['status']}", font=("Arial", 12, "bold"),
                 bg=COLOR_CONTENT_BG, fg=status_color).pack(pady=5)

        detail_f = tk.LabelFrame(info_f, text="详细信息", font=("Arial", 12, "bold"),
                                  bg=COLOR_CONTENT_BG, padx=20, pady=15)
        detail_f.pack(fill=tk.X, pady=10)
        hh = DB.get_household_info(r['id_number'])
        birth = hh.get('birth_date', '') if hh else ''
        info = [('身份证号', r['id_number']), ('性别', r['gender']), ('出生日期', birth),
                ('联系电话', r['phone']), ('住址', r['address']), ('任命日期', r['appointed_date']),
                ('最后更新', r['last_update'])]
        for i, (k, v) in enumerate(info):
            tk.Label(detail_f, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e', width=12,
                     bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).grid(row=i // 2, column=(i % 2) * 2, sticky='e', padx=20, pady=8)
            tk.Label(detail_f, text=str(v or '未填写'), font=("Arial", 10, "bold"), bg=COLOR_CONTENT_BG).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky='w', pady=8)

        # ---- 介绍与所属党派（官员可自行更新，名录/领导人页展示） ----
        intro_f = tk.LabelFrame(info_f, text="📝 介绍与所属党派", font=("Arial", 12, "bold"),
                                bg=COLOR_CONTENT_BG, padx=20, pady=12)
        intro_f.pack(fill=tk.X, pady=10)
        _party = ''
        _intro = ''
        try:
            _party = r['party'] or ''
            _intro = r['intro'] or ''
        except Exception:
            pass
        _irow = tk.Frame(intro_f, bg=COLOR_CONTENT_BG)
        _irow.pack(fill='x')
        tk.Label(_irow, text='所属党派：', font=("Arial", 10, "bold"),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(side='left')
        tk.Label(_irow, text=_party or '未填写', font=("Arial", 10, "bold"),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).pack(side='left')
        make_flat_button(_irow, '✏️ 编辑介绍', lambda: self._edit_my_intro(r['id']),
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=12, height=1).pack(side='right')
        tk.Label(intro_f, text=_intro or '尚未填写个人介绍，点击右上角「编辑介绍」补充。',
                 font=("Arial", 10), bg='#f4f6fa', fg='#444', wraplength=780,
                 justify='left', anchor='w', padx=10, pady=8).pack(fill='x', pady=(8, 0))

        # ---- 任期信息（新需求：任期起止 / 卸任日 / 连任申请 / 自动提交开关） ----
        ten_f = tk.LabelFrame(info_f, text="⏳ 任期与连任", font=("Arial", 12, "bold"),
                              bg=COLOR_CONTENT_BG, padx=20, pady=12)
        ten_f.pack(fill=tk.X, pady=10)
        try:
            _ten = DB.official_tenure(dict(r))
        except Exception:
            _ten = {'kind': '—', 'term_text': '—', 'resign_date': '—', 'terms': 0, 'note': ''}
        ten_info = [('任期类型', _ten['kind']), ('任期起止', _ten['term_text']),
                    ('卸任日', _ten['resign_date']),
                    ('当选届数', f"{_ten['terms']} 届" if _ten.get('terms') else '—')]
        for i, (k, v) in enumerate(ten_info):
            tk.Label(ten_f, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e', width=10,
                     bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).grid(row=i // 2, column=(i % 2) * 2, sticky='e', padx=12, pady=6)
            tk.Label(ten_f, text=str(v or '—'), font=("Arial", 10, "bold"),
                     bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky='w', pady=6)
        if _ten.get('note'):
            tk.Label(ten_f, text='📌 ' + _ten['note'], font=("Arial", 9), bg='#f4f6fa',
                     fg='#666', wraplength=760, justify='left', padx=8, pady=5).grid(
                row=2, column=0, columnspan=4, sticky='we', pady=(4, 0))

        is_elective = (r['position'] or '') in ('总统', '副总统')
        if r['status'] == '在职' and not is_elective:
            renew_f = tk.Frame(ten_f, bg=COLOR_CONTENT_BG)
            renew_f.grid(row=3, column=0, columnspan=4, sticky='we', pady=(10, 2))
            _ts = (r['term_end'] or '').strip()
            _rem = 9999
            try:
                _rem = (datetime.strptime(_ts[:10], '%Y-%m-%d') - datetime.now()).days
            except Exception:
                pass
            _can_apply = 0 <= _rem <= 30
            _pending_ra = len(DB.my_renewals(r['id'])) > 0 and any(
                x['status'] in ('待审核', '自动提交') for x in DB.my_renewals(r['id']))
            _apply_btn = make_flat_button(
                renew_f, '📩 提交连任申请', lambda: self._submit_renewal(r['id']),
                COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=16, height=1)
            _apply_btn.pack(side='left', padx=6)
            if not _can_apply:
                _apply_btn.config(state='disabled')
                tk.Label(renew_f, text=('⏳ 任期结束前 30 天内可提交连任申请' if _rem > 30 else '⛔ 任期已满，无法申请'),
                         font=("Arial", 9), fg=COLOR_TEXT_GRAY, bg=COLOR_CONTENT_BG).pack(side='left', padx=8)
            else:
                tk.Label(renew_f, text=f'⏰ 距卸任还有 {_rem} 天，可提交连任申请',
                         font=("Arial", 9), fg=COLOR_WARNING, bg=COLOR_CONTENT_BG).pack(side='left', padx=8)
            _auto_var = tk.IntVar(value=1 if r['auto_renewal'] else 0)
            _auto_cb = tk.Checkbutton(
                renew_f, text='结束前 10 天未提交时由系统自动提交连任申请', variable=_auto_var,
                font=("Arial", 9), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY, activebackground=COLOR_CONTENT_BG,
                command=lambda: DB.set_auto_renewal(r['id'], bool(_auto_var.get())))
            _auto_cb.pack(side='left', padx=12)

            # 我的连任申请列表
            _ra_rows = DB.my_renewals(r['id'])
            if _ra_rows:
                tk.Label(ten_f, text='我的连任申请：', font=("Arial", 10, "bold"),
                         bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).grid(
                    row=4, column=0, columnspan=4, sticky='w', pady=(10, 2))
                _st_color = {'待审核': COLOR_WARNING, '自动提交': COLOR_WARNING, '已通过': COLOR_SUCCESS,
                             '已驳回': COLOR_DANGER, '自动通过': COLOR_SUCCESS}
                for _ri, _ra in enumerate(_ra_rows[:4]):
                    _txt = (f"{_ra['submit_method']}提交 · 拟延任至 {_ra['new_term_end']} · "
                            f"{_ra['submitted_at'][:10]}")
                    tk.Label(ten_f, text=_txt, font=("Arial", 9), bg=COLOR_CONTENT_BG,
                             fg='#444').grid(row=5 + _ri * 2, column=0, columnspan=3, sticky='w', padx=16)
                    tk.Label(ten_f, text=_ra['status'], font=("Arial", 9, "bold"), bg=COLOR_CONTENT_BG,
                             fg=_st_color.get(_ra['status'], COLOR_TITLE)).grid(
                        row=5 + _ri * 2, column=3, sticky='w')
                    if _ra['review_note']:
                        tk.Label(ten_f, text='　↳ ' + _ra['review_note'], font=("Arial", 9),
                                 bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).grid(
                            row=5 + _ri * 2 + 1, column=0, columnspan=4, sticky='w', padx=16)

    def _upload_my_portrait(self, oid):
        """官员上传/更换自己的肖像：选图 → 裁剪（可拖动缩放）→ 存 400x400 → 写库"""
        if not HAS_PIL:
            messagebox.showerror('无法上传', '当前环境缺少 Pillow 图片库，无法处理肖像图片。')
            return
        path = filedialog.askopenfilename(
            title='选择肖像图片',
            filetypes=[('图片文件', '*.png *.jpg *.jpeg *.bmp *.gif *.webp'), ('所有文件', '*.*')])
        if not path:
            return

        def _after_crop(box):
            rel, err = save_official_portrait(path, oid, box)
            if not rel:
                messagebox.showerror('上传失败', err or '图片处理失败')
                return
            try:
                conn = DB.get_conn(GOV_DB)
                conn.execute("UPDATE officials SET photo=?, last_update=? WHERE id=?",
                             (rel, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), oid))
                conn.commit()
                conn.close()
            except Exception as e:
                messagebox.showerror('保存失败', f'写入数据库失败：{e}')
                return
            messagebox.showinfo('上传成功', '肖像已更新，官员名录与领导人肖像页同步生效。')
            self._show_my_info()

        try:
            PortraitCropDialog(self.main_frame, path, _after_crop)
        except Exception as e:
            messagebox.showerror('无法打开图片', f'该图片无法读取：{e}')

    def _delete_my_portrait(self, oid):
        """删除自己的肖像（恢复默认占位图）"""
        if not messagebox.askyesno('确认', '确定删除当前肖像？'):
            return
        try:
            conn = DB.get_conn(GOV_DB)
            row = conn.execute("SELECT photo FROM officials WHERE id=?", (oid,)).fetchone()
            old = row['photo'] if row else ''
            conn.execute("UPDATE officials SET photo='', last_update=? WHERE id=?",
                         (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), oid))
            conn.commit()
            conn.close()
            _abs = portrait_abs(old)
            if _abs and os.path.exists(_abs):
                try:
                    os.remove(_abs)
                except Exception:
                    pass
        except Exception as e:
            messagebox.showerror('删除失败', str(e))
            return
        self._show_my_info()

    def _edit_my_intro(self, oid):
        """编辑自己的所属党派与官员介绍"""
        conn = DB.get_conn(GOV_DB)
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT intro, party FROM officials WHERE id=?", (oid,)).fetchone()
        conn.close()

        win = tk.Toplevel(self.main_frame)
        win.title('编辑介绍与所属党派')
        win.geometry('560x440')
        win.configure(bg='white')
        win.transient(self.main_frame)
        win.grab_set()

        tk.Label(win, text='所属党派', font=("Arial", 12, "bold"), bg='white',
                 fg=COLOR_TITLE).pack(anchor='w', padx=22, pady=(18, 4))
        party_var = tk.StringVar(value=(r['party'] if r else '') or '')
        _pe = make_groove_entry(win, width=34)
        _pe.config(textvariable=party_var)
        _pe.pack(anchor='w', padx=22)
        tk.Label(win, text='（如：独立人士 / 联邦党 / 自由党，留空则显示“未填写”）',
                 font=("Arial", 9), bg='white', fg='#9a9a9f').pack(anchor='w', padx=22)

        tk.Label(win, text='官员介绍', font=("Arial", 12, "bold"), bg='white',
                 fg=COLOR_TITLE).pack(anchor='w', padx=22, pady=(16, 4))
        txt = tk.Text(win, height=9, width=58, font=("Arial", 10), relief='solid',
                      bd=1, wrap='word', padx=8, pady=6)
        txt.pack(padx=22, fill='both', expand=True)
        txt.insert('1.0', (r['intro'] if r else '') or '')
        tk.Label(win, text='介绍会展示在官员名录详情与「总统与副总统肖像」页。',
                 font=("Arial", 9), bg='white', fg='#9a9a9f').pack(anchor='w', padx=22, pady=(4, 0))

        def _save():
            intro = txt.get('1.0', 'end').strip()
            party = party_var.get().strip()
            try:
                conn2 = DB.get_conn(GOV_DB)
                conn2.execute("UPDATE officials SET intro=?, party=?, last_update=? WHERE id=?",
                              (intro, party, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), oid))
                conn2.commit()
                conn2.close()
            except Exception as e:
                messagebox.showerror('保存失败', str(e))
                return
            win.destroy()
            messagebox.showinfo('保存成功', '介绍与所属党派已更新。')
            self._show_my_info()

        _bf = tk.Frame(win, bg='white')
        _bf.pack(pady=14)
        make_flat_button(_bf, '💾 保存', _save, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(side='left', padx=6)
        make_flat_button(_bf, '取消', win.destroy, '#8e8e93', '#6c6c70',
                         width=10, height=1).pack(side='left', padx=6)

    def _submit_renewal(self, oid):
        ok, msg, _rid = DB.submit_renewal(self.user, '手动')
        if ok:
            messagebox.showinfo('提交成功', msg)
            self._show_my_info()
        else:
            messagebox.showwarning('提示', msg)

    # ========== 上级：官员任期管理（总统/副总统管所有官员，部长/局长/署长/处长管本部门） ==========
    def _show_tenure_mgmt(self):
        self._clear_main()
        is_top = (self.user.get('position') or '') in ('总统', '副总统')
        title = '🗓️ 官员任期管理' if is_top else '🗓️ 本部门官员任期管理'
        make_page_title(self.main_frame, title).pack(pady=15)
        tk.Label(self.main_frame, text=f'管理范围：{DB.tenure_scope_label(self.user)}'
                                       f'　·　任期届满自动卸任；调整任期后官员下次登录须重新签署协议'
                                       f'　·　支持搜索与批量调整',
                 font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 8))

        # ---- 搜索栏（姓名/身份证/职务/部门） ----
        search_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        search_f.pack(fill=tk.X, padx=30, pady=4)
        tk.Label(search_f, text='搜索官员（姓名/身份证/职务/部门）：', font=("Arial", 11),
                 bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        kw_e = make_groove_entry(search_f, width=24)
        kw_e.pack(side='left', padx=5)

        list_f = tk.LabelFrame(self.main_frame, text="官员任期列表（Ctrl/Shift 可多选）", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '姓名', '部门', '职务', '状态', '任期起', '任期止', '剩余天数')
        tree_frame, tree = make_tree(list_f, cols, {'id': 36, '姓名': 80, '部门': 90, '职务': 100,
                                           '状态': 60, '任期起': 100, '任期止': 100, '剩余天数': 70}, height=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        tree.configure(selectmode='extended')

        def load(kw=''):
            for item in tree.get_children():
                tree.delete(item)
            rows = DB.manageable_officials(self.user, kw)
            today = datetime.now()
            for o in rows:
                te = (o['term_end'] or '').strip()
                rem = ''
                if te:
                    try:
                        rem = (datetime.strptime(te[:10], '%Y-%m-%d') - today).days
                        rem = f'{rem} 天' if rem >= 0 else '已到期'
                    except Exception:
                        rem = ''
                _st = o['status']
                tree.insert('', 'end', values=(
                    o['id'], o['name'], o['department'], o['position'], _st,
                    (o['term_start'] or '')[:10], (o['term_end'] or '')[:10], rem))

        def do_search(_=None):
            load(kw_e.get().strip())

        def show_all():
            kw_e.delete(0, 'end')
            load()

        kw_e.bind('<Return>', do_search)
        make_flat_button(search_f, '🔍 搜索', do_search, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(side='left', padx=5)
        make_flat_button(search_f, '显示全部', show_all, COLOR_GRAY, COLOR_GRAY,
                         width=10, height=1).pack(side='left', padx=5)

        load()

        def _selected_rows():
            out = []
            for s in tree.selection():
                v = tree.item(s)['values']
                out.append({'oid': v[0], 'name': v[1], 'dept': v[2], 'pos': v[3],
                            'ts': v[5], 'te': v[6]})
            return out

        def set_tenure():
            sel = _selected_rows()
            if not sel:
                messagebox.showwarning('提示', '请选择一位官员')
                return
            if len(sel) > 1:
                messagebox.showinfo('提示', '单次调整请只选择一位官员；多选请使用“批量调整任期”')
                return
            oid, name, dept, pos = sel[0]['oid'], sel[0]['name'], sel[0]['dept'], sel[0]['pos']
            cur_ts, cur_te = sel[0]['ts'], sel[0]['te']
            if pos in ('总统', '副总统'):
                messagebox.showinfo('提示', '总统/副总统由大选产生，任期由大选记录确定，无需手动调整')
                return
            win = tk.Toplevel(self.root)
            win.title(f'调整任期 - {name}')
            win.geometry('360x240')
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text=f'调整官员：{name}（{dept}{pos}）', font=("Arial", 12, "bold")).pack(pady=12)
            tf = tk.Frame(win)
            tf.pack(pady=8)
            tk.Label(tf, text='起任日期：').grid(row=0, column=0, padx=5, pady=6)
            ts_e = make_groove_entry(tf, width=13)
            ts_e.insert(0, (cur_ts or '') or datetime.now().strftime('%Y-%m-%d'))
            ts_e.grid(row=0, column=1, padx=5, pady=6)
            tk.Label(tf, text='卸任日期：').grid(row=1, column=0, padx=5, pady=6)
            te_e = make_groove_entry(tf, width=13)
            te_e.insert(0, (cur_te or '') or (datetime.now() + timedelta(days=365 * 2)).strftime('%Y-%m-%d'))
            te_e.grid(row=1, column=1, padx=5, pady=6)
            tk.Label(win, text='格式 YYYY-MM-DD；调整后其下次登录须重新签署任职协议',
                     font=("Arial", 9), fg=COLOR_TEXT_GRAY).pack(pady=4)

            def confirm():
                nts, nte = ts_e.get().strip(), te_e.get().strip()
                try:
                    datetime.strptime(nts, '%Y-%m-%d')
                    datetime.strptime(nte, '%Y-%m-%d')
                except Exception:
                    messagebox.showwarning('提示', '请按 YYYY-MM-DD 格式填写')
                    return
                if nte < nts:
                    messagebox.showwarning('提示', '卸任日期不能早于起任日期')
                    return
                ok, msg = DB.set_tenure(oid, nts, nte, self.user['type'], self.user['id'], self.user['name'])
                messagebox.showinfo('成功' if ok else '提示', msg)
                win.destroy()
                load(kw_e.get().strip())
            make_flat_button(win, '确认调整', confirm, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12, height=1).pack(pady=12)

        def set_tenure_batch():
            sel = _selected_rows()
            if not sel:
                messagebox.showwarning('提示', '请选择要批量调整的官员（可 Ctrl/Shift 多选）')
                return
            win = tk.Toplevel(self.root)
            win.title(f'批量调整任期 - 共 {len(sel)} 名')
            win.geometry('400x280')
            win.transient(self.root)
            win.grab_set()
            names = '、'.join(r['name'] for r in sel[:6]) + ('…' if len(sel) > 6 else '')
            tk.Label(win, text=f'将对 {len(sel)} 名官员批量设置任期：{names}',
                     font=("Arial", 11, "bold"), wraplength=360, justify='left').pack(pady=10, padx=10)
            tf = tk.Frame(win)
            tf.pack(pady=8)
            tk.Label(tf, text='起任日期：').grid(row=0, column=0, padx=5, pady=6)
            ts_e = make_groove_entry(tf, width=13)
            ts_e.insert(0, datetime.now().strftime('%Y-%m-%d'))
            ts_e.grid(row=0, column=1, padx=5, pady=6)
            tk.Label(tf, text='卸任日期：').grid(row=1, column=0, padx=5, pady=6)
            te_e = make_groove_entry(tf, width=13)
            te_e.insert(0, (datetime.now() + timedelta(days=365 * 2)).strftime('%Y-%m-%d'))
            te_e.grid(row=1, column=1, padx=5, pady=6)
            tk.Label(win, text='格式 YYYY-MM-DD；总统/副总统将自动跳过；调整后需重新签署任职协议',
                     font=("Arial", 9), fg=COLOR_TEXT_GRAY).pack(pady=4)

            def confirm():
                nts, nte = ts_e.get().strip(), te_e.get().strip()
                try:
                    datetime.strptime(nts, '%Y-%m-%d')
                    datetime.strptime(nte, '%Y-%m-%d')
                except Exception:
                    messagebox.showwarning('提示', '请按 YYYY-MM-DD 格式填写')
                    return
                if nte < nts:
                    messagebox.showwarning('提示', '卸任日期不能早于起任日期')
                    return
                ok, msg, _done, _skipped = DB.set_tenure_batch(
                    [r['oid'] for r in sel], nts, nte, self.user['type'], self.user['id'], self.user['name'])
                messagebox.showinfo('成功' if ok else '提示', msg)
                win.destroy()
                load(kw_e.get().strip())
            make_flat_button(win, '确认批量调整', confirm, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                             width=14, height=1).pack(pady=12)

        def view_detail():
            sel = _selected_rows()
            if not sel:
                return
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM officials WHERE id=?", (sel[0]['oid'],)).fetchone()
            conn.close()
            if not r:
                return
            ten = DB.official_tenure(dict(r))
            self._official_detail_dialog(dict(r), ten)

        bar = tk.Frame(list_f, bg=COLOR_CONTENT_BG)
        bar.pack(pady=8)
        make_flat_button(bar, '🔄 刷新', lambda: load(kw_e.get().strip()), COLOR_GRAY, COLOR_GRAY, width=10, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '⏱️ 调整任期', set_tenure, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '📦 批量调整任期', set_tenure_batch, COLOR_PURPLE, COLOR_PURPLE, width=14, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '👤 查看档案', view_detail, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12, height=1).pack(side='left', padx=6)

    # ========== 上级：连任申请审批 ==========
    def _show_renewal_review(self):
        self._clear_main()
        make_page_title(self.main_frame, "📋 连任申请审批").pack(pady=15)
        tk.Label(self.main_frame, text='连任申请由上级审核；卸任前 3 天无人受理时系统自动通过',
                 font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 8))

        list_f = tk.LabelFrame(self.main_frame, text="待我审核的连任申请", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '姓名', '职务', '原任期', '拟延任至', '提交方式', '提交时间')
        tree_frame, tree = make_tree(list_f, cols, {'id': 36, '姓名': 80, '职务': 100, '原任期': 170,
                                           '拟延任至': 100, '提交方式': 80, '提交时间': 120}, height=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            for ra in DB.pending_renewals(self.user):
                tree.insert('', 'end', values=(
                    ra['id'], ra['official_name'], f"{ra['department']}{ra['position']}",
                    f"{ra['old_term_start']} 至 {ra['old_term_end']}", ra['new_term_end'],
                    ra['submit_method'], (ra['submitted_at'] or '')[:16]))

        load()

        def approve(agree=True):
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条申请')
                return
            rid = tree.item(sel[0])['values'][0]
            note = ''
            if not agree:
                note = simpledialog.askstring('驳回原因', '请输入驳回原因（可留空）：', parent=self.root)
                if note is None:
                    return
            ok, msg = DB.review_renewal(rid, self.user, agree, note)
            messagebox.showinfo('成功' if ok else '提示', msg)
            load()

        bar = tk.Frame(list_f, bg=COLOR_CONTENT_BG)
        bar.pack(pady=8)
        make_flat_button(bar, '🔄 刷新', load, COLOR_GRAY, COLOR_GRAY, width=10, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '✅ 同意连任', lambda: approve(True), COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '✖ 驳回', lambda: approve(False), COLOR_DANGER, COLOR_DANGER_HOVER, width=10, height=1).pack(side='left', padx=6)

    # ========== 上级：合同管理（对单个/多个官员发起合同，查看签署状态） ==========
    def _show_contract_mgmt(self):
        self._clear_main()
        is_top = (self.user.get('position') or '') in ('总统', '副总统')
        make_page_title(self.main_frame, '📄 合同管理').pack(pady=15)
        tk.Label(self.main_frame, text=f'管理范围：{DB.tenure_scope_label(self.user)}'
                                       f'　·　可对单个或多个官员发起合同并要求其签署',
                 font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 8))

        top_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        top_f.pack(fill=tk.X, padx=30)
        form_f = tk.LabelFrame(top_f, text="发起新合同", font=("Arial", 11, "bold"),
                               bg=COLOR_CONTENT_BG, padx=12, pady=8)
        form_f.pack(side='left', fill=tk.BOTH, expand=True)
        tk.Label(form_f, text='合同标题：', font=("Arial", 10), bg=COLOR_CONTENT_BG).grid(row=0, column=0, sticky='e', pady=4)
        title_e = make_groove_entry(form_f, width=30)
        title_e.grid(row=0, column=1, sticky='w', pady=4)
        tk.Label(form_f, text='合同内容：', font=("Arial", 10), bg=COLOR_CONTENT_BG).grid(row=1, column=0, sticky='ne', pady=4)
        content_t = tk.Text(form_f, width=34, height=4, font=("Arial", 10))
        content_t.grid(row=1, column=1, sticky='we', pady=4)
        tk.Label(form_f, text='签署官员（Ctrl 多选）：', font=("Arial", 10),
                 bg=COLOR_CONTENT_BG).grid(row=2, column=0, sticky='ne', pady=4)
        sel_lb = tk.Listbox(form_f, selectmode=tk.EXTENDED, width=34, height=6,
                            font=("Arial", 10), exportselection=False)
        sel_lb.grid(row=2, column=1, sticky='we', pady=4)
        _mng = DB.manageable_officials(self.user)
        _lb_map = {}
        for o in _mng:
            _lb_map[f'{o["name"]}（{o["department"]}{o["position"]}）'] = o['id']
        for k in _lb_map:
            sel_lb.insert('end', k)

        def create():
            oids = [_lb_map[sel_lb.get(i)] for i in sel_lb.curselection()]
            ok, msg, _cid = DB.create_contract(self.user, title_e.get().strip(),
                                               content_t.get('1.0', 'end').strip(), oids)
            messagebox.showinfo('成功' if ok else '提示', msg)
            if ok:
                title_e.delete(0, 'end')
                content_t.delete('1.0', 'end')
                load()

        make_flat_button(form_f, '📨 发起合同', create, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                         width=12, height=1).grid(row=3, column=1, sticky='w', pady=8)

        right_f = tk.LabelFrame(top_f, text="选择官员（快捷）", font=("Arial", 11, "bold"),
                                bg=COLOR_CONTENT_BG, padx=10, pady=8)
        right_f.pack(side='left', fill=tk.Y, padx=(10, 0))

        def sel_all():
            sel_lb.selection_clear(0, 'end')
            for i in range(sel_lb.size()):
                sel_lb.selection_set(i)
        make_flat_button(right_f, '全选', sel_all, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10, height=1).pack(pady=4)

        def sel_none():
            sel_lb.selection_clear(0, 'end')
        make_flat_button(right_f, '取消全选', sel_none, COLOR_GRAY, COLOR_GRAY, width=10, height=1).pack(pady=4)
        tk.Label(right_f, text=f'可发起对象：{len(_mng)} 人', font=("Arial", 9),
                 bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=6)

        list_f = tk.LabelFrame(self.main_frame, text="我发起的合同（签署进度）", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '合同标题', '发起时间', '签署进度', '详情')
        tree_frame, tree = make_tree(list_f, cols, {'id': 36, '合同标题': 220, '发起时间': 140,
                                           '签署进度': 110, '详情': 330}, height=8)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            for c in DB.contracts_for_issuer(self.user['id']):
                det = '；'.join(f"{t['official_name']}:{t['status']}" for t in c['targets'])
                tree.insert('', 'end', values=(
                    c['id'], c['title'], (c['created_at'] or '')[:16],
                    f"{c['signed_count']}/{c['total_count']}", det))

        load()

        def view_contract():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一份合同')
                return
            vals = tree.item(sel[0])['values']
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM gov_contracts WHERE id=?", (vals[0],)).fetchone()
            targets = conn.execute("SELECT * FROM contract_targets WHERE contract_id=? ORDER BY id",
                                   (vals[0],)).fetchall()
            conn.close()
            if not r:
                return
            win = tk.Toplevel(self.root)
            win.title(f'合同详情 · {r["title"]}')
            win.geometry('520x430')
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text=f'📄 {r["title"]}', font=("Arial", 13, "bold")).pack(pady=(14, 4))
            tk.Label(win, text=f'发起人：{r["issuer_name"]}　·　{r["created_at"][:16]}',
                     font=("Arial", 9), fg=COLOR_TEXT_GRAY).pack()
            body = tk.Label(win, text=r['content'], font=("Arial", 10), justify='left',
                            wraplength=470, bg='#f4f6fa', padx=10, pady=8)
            body.pack(fill='x', padx=20, pady=8)
            tk.Label(win, text='签署状态：', font=("Arial", 10, "bold")).pack(anchor='w', padx=24)
            for t in targets:
                col = COLOR_SUCCESS if t['status'] == '已签署' else (COLOR_DANGER if t['status'] == '已拒绝' else COLOR_WARNING)
                tk.Label(win, text=f"　· {t['official_name']}：{t['status']}",
                         font=("Arial", 10), fg=col).pack(anchor='w', padx=34)
            make_flat_button(win, '关 闭', win.destroy, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=12, height=1).pack(pady=12)

        bar = tk.Frame(list_f, bg=COLOR_CONTENT_BG)
        bar.pack(pady=8)
        make_flat_button(bar, '🔄 刷新', load, COLOR_GRAY, COLOR_GRAY, width=10, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '📄 查看详情', view_contract, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12, height=1).pack(side='left', padx=6)

    # ========== 官员：我的合同 ==========
    def _show_my_contracts(self):
        self._clear_main()
        make_page_title(self.main_frame, "📄 我的合同").pack(pady=15)
        tk.Label(self.main_frame, text='上级发起的合同在此签署；签署后具有约束效力',
                 font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 8))

        list_f = tk.LabelFrame(self.main_frame, text="发给我的合同", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '合同标题', '发起人', '发起时间', '状态', '内容')
        tree_frame, tree = make_tree(list_f, cols, {'id': 36, '合同标题': 180, '发起人': 90,
                                           '发起时间': 130, '状态': 60, '内容': 380}, height=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            for mc in DB.contracts_for_official(self.user['id']):
                c = mc['contract'] or {}
                tree.insert('', 'end', values=(
                    mc['id'], c.get('title', ''), c.get('issuer_name', ''),
                    (c.get('created_at') or '')[:16], mc['status'], (c.get('content') or '')[:60]))

        load()

        def view():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一份合同')
                return
            vals = tree.item(sel[0])['values']
            conn = DB.get_conn(GOV_DB)
            t = conn.execute("SELECT * FROM contract_targets WHERE id=?", (vals[0],)).fetchone()
            c = conn.execute("SELECT * FROM gov_contracts WHERE id=?", (t['contract_id'],)).fetchone()
            conn.close()
            if not t or not c:
                return
            win = tk.Toplevel(self.root)
            win.title(f'合同详情 · {c["title"]}')
            win.geometry('540x460')
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text=f'📄 {c["title"]}', font=("Arial", 14, "bold")).pack(pady=(14, 4))
            tk.Label(win, text=f'发起人：{c["issuer_name"]}　·　发起时间：{(c["created_at"] or "")[:16]}',
                     font=("Arial", 9), fg=COLOR_TEXT_GRAY).pack()
            body = tk.Label(win, text=c['content'], font=("Arial", 11), justify='left',
                            wraplength=480, bg='#f4f6fa', padx=12, pady=10)
            body.pack(fill='x', padx=22, pady=10)
            tk.Label(win, text=f'当前状态：{t["status"]}', font=("Arial", 10, "bold"),
                     fg=COLOR_WARNING if t['status'] == '待签署' else COLOR_SUCCESS).pack(pady=4)

            def do_sign(agree):
                ok, msg = DB.sign_contract(t['id'], self.user['id'], agree)
                messagebox.showinfo('成功' if ok else '提示', msg)
                win.destroy()
                load()

            if t['status'] == '待签署':
                bf = tk.Frame(win)
                bf.pack(pady=12)
                make_flat_button(bf, '✅ 签署合同', lambda: do_sign(True), COLOR_SUCCESS,
                                 COLOR_SUCCESS_HOVER, width=14, height=1).pack(side='left', padx=8)
                make_flat_button(bf, '✖ 拒绝签署', lambda: do_sign(False), COLOR_DANGER,
                                 COLOR_DANGER_HOVER, width=14, height=1).pack(side='left', padx=8)
            make_flat_button(win, '关 闭', win.destroy, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=12, height=1).pack(pady=8)

        bar = tk.Frame(list_f, bg=COLOR_CONTENT_BG)
        bar.pack(pady=8)
        make_flat_button(bar, '🔄 刷新', load, COLOR_GRAY, COLOR_GRAY, width=10, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '📄 查看并签署', view, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14, height=1).pack(side='left', padx=6)
        make_flat_button(bar, '📨 我的消息', self._show_my_messages, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12, height=1).pack(side='left', padx=6)

    # ========== 我的消息（民众/官员通用） ==========
    def _show_my_messages(self):
        self._clear_main()
        make_page_title(self.main_frame, "📨 我的消息").pack(pady=20)
        if self.user['type'] == 'admin':
            rid = str(self.user['id'])
        else:
            rid = self.user['id_number']
        rtype = self.user['type']

        list_frame = tk.LabelFrame(self.main_frame, text="站内消息", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '标题', '内容', '状态', '时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '标题': 200, '内容': 420, '状态': 80, '时间': 140}, height=15)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute(
                "SELECT * FROM messages WHERE (receiver_type=? AND receiver_id=?) OR receiver_type='all' ORDER BY id DESC",
                (rtype, rid)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], r['content'][:60] + ('...' if len(r['content']) > 60 else ''),
                                                '已读' if r['is_read'] else '未读', r['created_at']))
            self._refresh_status()

        load()

        def read_msg():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条消息')
                return
            mid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if r and not r['is_read']:
                conn.execute("UPDATE messages SET is_read=1 WHERE id=?", (mid,))
                conn.commit()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'消息 - {r["title"]}')
            win.geometry('560x420')
            win.transient(self.root)
            tk.Label(win, text=f'【{r["title"]}】', font=("Arial", 14, "bold")).pack(pady=15)
            tk.Label(win, text=f'时间：{r["created_at"]}', font=("Arial", 10), fg=COLOR_TEXT_GRAY).pack()
            txt = scrolledtext.ScrolledText(win, font=("Arial", 11), height=10, wrap='word')
            txt.pack(fill='both', expand=True, padx=15, pady=10)
            txt.insert('1.0', r['content'])
            txt.config(state='disabled')
            load()

        def delete_msg():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条消息')
                return
            mid = tree.item(sel[0])['values'][0]
            if messagebox.askyesno('确认', '确定删除该消息吗？'):
                conn = DB.get_conn(GOV_DB)
                conn.execute("DELETE FROM messages WHERE id=?", (mid,))
                conn.commit()
                conn.close()
                load()

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '📄 查看消息', read_msg, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '🗑️ 删除消息', delete_msg, COLOR_DANGER, COLOR_DANGER_HOVER, width=12, height=1).pack(side='left', padx=5)

    # ========== 民众：我的户籍 ==========
    def _show_my_household(self):
        self._clear_main()
        make_page_title(self.main_frame, "🪪 我的户籍信息").pack(pady=20)
        info = DB.get_household_info(self.user['id_number'])
        if not info:
            tk.Label(self.main_frame, text="未查询到户籍信息", font=("Arial", 16),
                     bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=50)
            return

        card = tk.LabelFrame(self.main_frame, text="户籍登记信息（数据来源：蛋国户籍系统）",
                             font=("Arial", 12, "bold"), bg=COLOR_CONTENT_BG, padx=30, pady=20)
        card.pack(fill=tk.BOTH, expand=True, padx=50, pady=20)
        info_items = [('姓名', info.get('name', '')), ('身份证号', info.get('id_number', '')),
                      ('性别', info.get('gender', '')), ('出生日期', info.get('birth_date', '')),
                      ('住址', info.get('address', '')), ('登记日期', info.get('registered_date', '')),
                      ('最后更新', info.get('last_update', '')), ('状态', info.get('status', ''))]
        for i, (k, v) in enumerate(info_items):
            tk.Label(card, text=f'{k}：', font=("Arial", 12, "bold"), anchor='e', width=12,
                     bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).grid(row=i // 2, column=(i % 2) * 2, sticky='e', padx=20, pady=12)
            tk.Label(card, text=str(v or '无'), font=("Arial", 12, "bold"), bg=COLOR_CONTENT_BG).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky='w', pady=12)
    # ========== 管理员：办事事项管理 ==========
    def _show_items_admin(self):
        self._clear_main()
        make_page_title(self.main_frame, "📚 办事事项管理").pack(pady=20)

        form_f = tk.LabelFrame(self.main_frame, text="新增/编辑办事事项", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=15, pady=10)
        form_f.pack(fill=tk.X, padx=30, pady=5)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=3)
        tk.Label(row1, text="事项编号：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        code_e = make_groove_entry(row1, width=10)
        code_e.pack(side='left', padx=5)
        tk.Label(row1, text="事项名称：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        name_e = make_groove_entry(row1, width=26)
        name_e.pack(side='left', padx=5)
        tk.Label(row1, text="分类：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        cat_var = tk.StringVar(value=SERVICE_CATEGORIES[0])
        ttk.Combobox(row1, textvariable=cat_var, values=SERVICE_CATEGORIES, font=("Arial", 11),
                     width=10, state='readonly').pack(side='left')
        tk.Label(row1, text="承办部门：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        dept_var = tk.StringVar(value='国务院')
        ttk.Combobox(row1, textvariable=dept_var, values=DEPARTMENTS, font=("Arial", 11),
                     width=10, state='readonly').pack(side='left')
        row2 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row2.pack(fill='x', pady=3)
        tk.Label(row2, text="办理时限：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        days_e = make_groove_entry(row2, width=12)
        days_e.pack(side='left', padx=5)
        tk.Label(row2, text="收费标准：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        fee_e = make_groove_entry(row2, width=18)
        fee_e.pack(side='left', padx=5)
        tk.Label(row2, text="法律依据：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        law_e = make_groove_entry(row2, width=30)
        law_e.pack(side='left', padx=5)
        tk.Label(form_f, text="事项说明：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5, pady=(5, 0))
        desc_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=3, wrap='word')
        desc_e.pack(fill='x', padx=5, pady=3)
        tk.Label(form_f, text="所需材料：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5)
        mat_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=3, wrap='word')
        mat_e.pack(fill='x', padx=5, pady=3)

        def clear_form():
            code_e.delete(0, 'end')
            name_e.delete(0, 'end')
            days_e.delete(0, 'end')
            fee_e.delete(0, 'end')
            law_e.delete(0, 'end')
            desc_e.delete('1.0', 'end')
            mat_e.delete('1.0', 'end')
            cat_var.set(SERVICE_CATEGORIES[0])
            dept_var.set('国务院')

        def add_item():
            code = code_e.get().strip()
            name = name_e.get().strip()
            if not code or not name:
                messagebox.showwarning('提示', '请填写事项编号和名称')
                return
            conn = DB.get_conn(GOV_DB)
            if conn.execute("SELECT id FROM service_items WHERE item_code=?", (code,)).fetchone():
                conn.close()
                messagebox.showwarning('提示', '事项编号已存在')
                return
            conn.execute('''INSERT INTO service_items (item_code,name,category,department,description,
                required_materials,legal_basis,processing_days,fee,is_active,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,1,?)''',
                (code, name, cat_var.get(), dept_var.get(), desc_e.get('1.0', 'end').strip(),
                 mat_e.get('1.0', 'end').strip(), law_e.get().strip(), days_e.get().strip(),
                 fee_e.get().strip(), now_str()))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '新增事项', f'新增事项《{name}》')
            messagebox.showinfo('成功', '事项新增成功')
            clear_form()
            load_items()

        btn_f = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=6)
        make_flat_button(btn_f, '➕ 新增事项', add_item, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12, height=1).pack(side='left', padx=6)
        make_flat_button(btn_f, '🔄 清空表单', clear_form, COLOR_GRAY, COLOR_GRAY, width=12, height=1).pack(side='left', padx=6)

        list_f = tk.LabelFrame(self.main_frame, text="服务事项目录", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '事项编号', '事项名称', '分类', '承办部门', '办理时限', '收费标准', '状态')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '事项编号': 80, '事项名称': 200, '分类': 90,
                                            '承办部门': 80, '办理时限': 90, '收费标准': 100, '状态': 70}, height=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load_items():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM service_items ORDER BY item_code").fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['item_code'], r['name'], r['category'],
                                                r['department'], r['processing_days'], r['fee'],
                                                '启用' if r['is_active'] else '停用'))

        load_items()

        def toggle_item():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一项事项')
                return
            iid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_items WHERE id=?", (iid,)).fetchone()
            conn.execute("UPDATE service_items SET is_active=? WHERE id=?",
                         (0 if r['is_active'] else 1, iid))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '启停事项', f"{'停用' if r['is_active'] else '启用'}事项{r['item_code']}")
            load_items()

        def delete_item():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一项事项')
                return
            iid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_items WHERE id=?", (iid,)).fetchone()
            conn.close()
            if not messagebox.askyesno('确认', f'确定删除事项《{r["name"]}》吗？'):
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute("DELETE FROM service_items WHERE id=?", (iid,))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '删除事项', f'删除事项{r["item_code"]}')
            messagebox.showinfo('成功', '事项已删除')
            load_items()

        op_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        op_f.pack(pady=8)
        make_flat_button(op_f, '⏸️ 启/停用', toggle_item, COLOR_WARNING, COLOR_WARNING, width=10, height=1).pack(side='left', padx=5)
        make_flat_button(op_f, '🗑️ 删除事项', delete_item, COLOR_DANGER, COLOR_DANGER_HOVER, width=10, height=1).pack(side='left', padx=5)

    # ========== 管理员：办件管理 ==========
    def _show_cases_admin(self):
        self._clear_main()
        make_page_title(self.main_frame, "🗂️ 办件管理").pack(pady=20)

        filter_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        filter_f.pack(pady=5)
        tk.Label(filter_f, text="状态：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        st_var = tk.StringVar(value='全部')
        ttk.Combobox(filter_f, textvariable=st_var, values=['全部'] + CASE_STATUS,
                      font=("Arial", 11), width=10, state='readonly').pack(side='left', padx=5)
        tk.Label(filter_f, text="搜索：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        kw_e = make_groove_entry(filter_f, width=18)
        kw_e.pack(side='left', padx=5)

        list_frame = tk.LabelFrame(self.main_frame, text="全部办件", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '办件编号', '事项名称', '申请人', '承办部门', '状态', '办理人', '提交时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '办件编号': 150, '事项名称': 180, '申请人': 100,
                                                '承办部门': 80, '状态': 80, '办理人': 80, '提交时间': 130}, height=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            st = st_var.get()
            kw = f'%{kw_e.get().strip()}%'
            if st == '全部':
                rows = conn.execute("SELECT * FROM service_cases WHERE item_name LIKE ? OR applicant_name LIKE ? OR case_no LIKE ? ORDER BY id DESC",
                                    (kw, kw, kw)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM service_cases WHERE status=? AND (item_name LIKE ? OR applicant_name LIKE ? OR case_no LIKE ?) ORDER BY id DESC",
                                    (st, kw, kw, kw)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['case_no'], r['item_name'], r['applicant_name'],
                                                r['department'], r['status'], r['handler_name'] or '-', r['created_at']))

        load()
        st_var.trace_add('write', lambda *a: load())
        tk.Button(filter_f, text="查询", command=load, width=8, bg=COLOR_PRIMARY, fg='white',
                  relief=tk.FLAT, cursor='hand2').pack(side='left', padx=5)

        def view_case():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条办件')
                return
            sid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE id=?", (sid,)).fetchone()
            conn.close()
            content = f"【申请内容】\n{r['content']}\n\n【办理进度】\n{r['progress'] or '暂无'}\n\n【处理意见】\n{r['handle_comment'] or '暂无'}\n\n【评价】\n{'★' * (r['rating'] or 0) + ' ' + (r['rating_comment'] or '') if r['rating'] else '未评价'}"
            self._popup_detail(f'办件详情 - {r["case_no"]}',
                               [('办件编号', r['case_no']), ('事项', r['item_name']), ('申请人', r['applicant_name']),
                                ('承办部门', r['department']), ('当前状态', r['status']), ('办理人', r['handler_name'] or '-'),
                                ('提交时间', r['created_at']), ('办结时间', r['finished_at'] or '-')],
                               '详细信息：', content)

        def reassign_case():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条办件')
                return
            sid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE id=?", (sid,)).fetchone()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'转派办件 - {r["case_no"]}')
            win.geometry('360x220')
            win.transient(self.root)
            win.grab_set()
            tk.Label(win, text=f'办件：{r["case_no"]}（{r["item_name"]}）', font=("Arial", 12, "bold")).pack(pady=10)
            tk.Label(win, text='转派到部门：').pack()
            dept_var = tk.StringVar(value=r['department'])
            ttk.Combobox(win, textvariable=dept_var, values=DEPARTMENTS, state='readonly').pack(pady=5)
            tk.Label(win, text='办理人（身份证号，可留空）：').pack()
            handler_e = make_groove_entry(win, width=26)
            handler_e.pack(pady=5)
            def confirm():
                hname = ''
                hid = handler_e.get().strip()
                if hid:
                    conn = DB.get_conn(GOV_DB)
                    off = conn.execute("SELECT name FROM officials WHERE id_number=? AND status='在职'", (hid,)).fetchone()
                    conn.close()
                    if not off:
                        messagebox.showwarning('提示', '未找到该在职官员')
                        return
                    hname = off['name']
                conn = DB.get_conn(GOV_DB)
                conn.execute("UPDATE service_cases SET department=?, handler_id=?, handler_name=?, status='待受理', progress=progress || char(10) || ? WHERE id=?",
                             (dept_var.get(), hid, hname, f"{now_str()} 管理员转派至{dept_var.get()}部门", sid))
                conn.commit()
                conn.close()
                DB.log('admin', self.user['id'], self.user['name'], '转派办件', f'办件{r["case_no"]}转派至{dept_var.get()}')
                messagebox.showinfo('成功', '办件已转派')
                win.destroy()
                load()
            make_flat_button(win, '确认转派', confirm, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12).pack(pady=12)

        def force_close():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条办件')
                return
            sid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE id=?", (sid,)).fetchone()
            conn.close()
            if r['status'] == '已办结':
                messagebox.showinfo('提示', '该办件已办结')
                return
            if not messagebox.askyesno('确认', f'确定强制办结办件 {r["case_no"]} 吗？'):
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute("UPDATE service_cases SET status='已办结', handle_comment=handle_comment || char(10) || ?, finished_at=?, rating=0 WHERE id=?",
                         (f"{now_str()} 管理员强制办结", now_str(), sid))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '强制办结', f'办件{r["case_no"]}强制办结')
            DB.send_message('citizen', r['applicant_id'], '办件办结通知',
                            f'您的办件 {r["case_no"]}（{r["item_name"]}）已办结。')
            messagebox.showinfo('成功', '办件已办结')
            load()

        def delete_case():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条办件')
                return
            sid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_cases WHERE id=?", (sid,)).fetchone()
            conn.close()
            if not messagebox.askyesno('确认', f'确定删除办件 {r["case_no"]} 吗？'):
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute("DELETE FROM service_cases WHERE id=?", (sid,))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '删除办件', f'删除办件{r["case_no"]}')
            messagebox.showinfo('成功', '办件已删除')
            load()

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '📄 查看详情', view_case, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '🔀 转派办件', reassign_case, COLOR_WARNING, COLOR_WARNING, width=10, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '🏁 强制办结', force_close, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=10, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '🗑️ 删除办件', delete_case, COLOR_DANGER, COLOR_DANGER_HOVER, width=10, height=1).pack(side='left', padx=5)

    # ========== 管理员：预约管理 ==========
    def _show_appointments_admin(self):
        self._clear_main()
        make_page_title(self.main_frame, "📅 预约管理").pack(pady=20)

        list_f = tk.LabelFrame(self.main_frame, text="全部预约", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '预约号', '事项', '申请人', '预约日期', '时段', '承办部门', '状态')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '预约号': 150, '事项': 200, '申请人': 90,
                                            '预约日期': 100, '时段': 110, '承办部门': 80, '状态': 80}, height=13)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM service_appointments ORDER BY appt_date DESC, id DESC").fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['appt_no'], r['item_name'], r['applicant_name'],
                                                r['appt_date'], r['appt_time'], r['department'], r['status']))

        load()

        def set_status(new_status, action):
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条预约')
                return
            aid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM service_appointments WHERE id=?", (aid,)).fetchone()
            conn.execute("UPDATE service_appointments SET status=? WHERE id=?", (new_status, aid))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '预约管理', f'预约{r["appt_no"]}{action}')
            DB.send_message('citizen', r['applicant_id'], '预约办理通知',
                            f'您的预约（{r["item_name"]}，{r["appt_date"]} {r["appt_time"]}）已{action}。')
            messagebox.showinfo('成功', f'预约已{action}')
            load()

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '✅ 到场受理', lambda: set_status('已受理', '到场受理'), COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '🏁 完成办理', lambda: set_status('已完成', '完成办理'), COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=10, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '❌ 取消预约', lambda: set_status('已取消', '取消'), COLOR_DANGER, COLOR_DANGER_HOVER, width=10, height=1).pack(side='left', padx=5)

    # ========== 管理员：公文管理 ==========
    def _show_docs_admin(self):
        self._clear_main()
        make_page_title(self.main_frame, "📄 公文管理（全部）").pack(pady=20)

        list_f = tk.LabelFrame(self.main_frame, text="全部公文", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '文号', '标题', '类型', '紧急', '发文部门', '收文部门', '发送人', '状态', '时间')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '文号': 150, '标题': 200, '类型': 60, '紧急': 55,
                                            '发文部门': 90, '收文部门': 90, '发送人': 80, '状态': 80, '时间': 130}, height=13)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM official_docs ORDER BY id DESC").fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['doc_no'], r['title'], r['doc_type'], r['urgency'],
                                                r['from_department'], r['to_department'] or '-', r['sender_name'],
                                                r['status'], r['created_at']))

        load()

        def view_doc():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一份公文')
                return
            did = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM official_docs WHERE id=?", (did,)).fetchone()
            conn.close()
            self._popup_detail(f'公文详情 - {r["doc_no"]}',
                               [('文号', r['doc_no']), ('标题', r['title']), ('类型', r['doc_type']),
                                ('紧急程度', r['urgency']), ('发文部门', r['from_department']), ('收文部门', r['to_department'] or '-'),
                                ('发送人', r['sender_name']), ('状态', r['status']), ('时间', r['created_at'])],
                               '公文内容：', r['content'])

        def delete_doc():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一份公文')
                return
            did = tree.item(sel[0])['values'][0]
            if messagebox.askyesno('确认', '确定删除该公文吗？'):
                conn = DB.get_conn(GOV_DB)
                conn.execute("DELETE FROM official_docs WHERE id=?", (did,))
                conn.commit()
                conn.close()
                DB.log('admin', self.user['id'], self.user['name'], '删除公文', '删除一份公文')
                load()

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '📄 查看详情', view_doc, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '🗑️ 删除公文', delete_doc, COLOR_DANGER, COLOR_DANGER_HOVER, width=10, height=1).pack(side='left', padx=5)

    # ========== 管理员：政策文件管理 ==========
    def _show_policy_admin(self):
        self._clear_main()
        make_page_title(self.main_frame, "📜 政策文件管理").pack(pady=20)

        form_f = tk.LabelFrame(self.main_frame, text="发布政策文件", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=15, pady=10)
        form_f.pack(fill=tk.X, padx=30, pady=5)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=4)
        tk.Label(row1, text="文号：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        doc_no_e = make_groove_entry(row1, width=20)
        doc_no_e.pack(side='left', padx=5)
        tk.Label(row1, text="标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        title_e = make_groove_entry(row1, width=32)
        title_e.pack(side='left', padx=5)
        tk.Label(row1, text="分类：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        cat_var = tk.StringVar(value='政策法规')
        ttk.Combobox(row1, textvariable=cat_var, values=['政策法规', '通知文件', '规划计划', '工作报告', '其他'],
                      font=("Arial", 11), width=8, state='readonly').pack(side='left')
        row2 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row2.pack(fill='x', pady=4)
        tk.Label(row2, text="发文机关：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        dept_var = tk.StringVar(value='国务院')
        ttk.Combobox(row2, textvariable=dept_var, values=DEPARTMENTS, font=("Arial", 11),
                     width=10, state='readonly').pack(side='left')
        tk.Label(row2, text="发布日期(YYYY-MM-DD)：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        date_e = make_groove_entry(row2, width=14)
        date_e.insert(0, datetime.now().strftime('%Y-%m-%d'))
        date_e.pack(side='left', padx=5)
        tk.Label(form_f, text="文件全文：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5)
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=4, wrap='word')
        content_e.pack(fill='x', padx=5, pady=4)

        def publish():
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写标题和内容')
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO policy_docs (doc_no,title,category,issuing_department,issue_date,content,publisher,is_active,created_at)
                VALUES (?,?,?,?,?,?,?,1,?)''',
                (doc_no_e.get().strip(), title, cat_var.get(), dept_var.get(), date_e.get().strip(),
                 content, self.user['name'], now_str()))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '发布政策文件', f'发布《{title}》')
            messagebox.showinfo('成功', '政策文件发布成功')
            title_e.delete(0, 'end')
            doc_no_e.delete(0, 'end')
            content_e.delete('1.0', 'end')
            load_docs()

        make_flat_button(form_f, '📜 发布文件', publish, COLOR_WARNING, COLOR_WARNING, width=14).pack(pady=6)

        list_f = tk.LabelFrame(self.main_frame, text="已发布政策文件", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '文号', '标题', '分类', '发文机关', '发布日期', '状态')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '文号': 150, '标题': 260, '分类': 90,
                                            '发文机关': 90, '发布日期': 100, '状态': 70}, height=8)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load_docs():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM policy_docs ORDER BY id DESC").fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['doc_no'] or '-', r['title'], r['category'] or '-',
                                                r['issuing_department'] or '-', r['issue_date'] or '-',
                                                '公开' if r['is_active'] else '已下架'))

        load_docs()

        def view_doc():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一份文件')
                return
            did = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM policy_docs WHERE id=?", (did,)).fetchone()
            conn.close()
            self._popup_detail(f'政策文件 - {r["title"]}',
                               [('文号', r['doc_no'] or '-'), ('标题', r['title']), ('分类', r['category'] or '-'),
                                ('发文机关', r['issuing_department'] or '-'), ('发布日期', r['issue_date'] or '-'), ('发布人', r['publisher'])],
                               '文件全文：', r['content'])

        def toggle_doc():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一份文件')
                return
            did = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM policy_docs WHERE id=?", (did,)).fetchone()
            conn.execute("UPDATE policy_docs SET is_active=? WHERE id=?", (0 if r['is_active'] else 1, did))
            conn.commit()
            conn.close()
            load_docs()

        def delete_doc():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一份文件')
                return
            did = tree.item(sel[0])['values'][0]
            if messagebox.askyesno('确认', '确定删除该政策文件吗？'):
                conn = DB.get_conn(GOV_DB)
                conn.execute("DELETE FROM policy_docs WHERE id=?", (did,))
                conn.commit()
                conn.close()
                load_docs()

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=8)
        make_flat_button(btn_f, '📄 查看全文', view_doc, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '⏸️ 公开/下架', toggle_doc, COLOR_WARNING, COLOR_WARNING, width=12, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '🗑️ 删除文件', delete_doc, COLOR_DANGER, COLOR_DANGER_HOVER, width=10, height=1).pack(side='left', padx=5)

    # ========== 管理员：公告管理 ==========
    def _show_announcements(self):
        self._clear_main()
        make_page_title(self.main_frame, "📢 公告管理").pack(pady=20)

        pub_f = tk.LabelFrame(self.main_frame, text="发布公告", font=("Arial", 12, "bold"),
                              bg=COLOR_CONTENT_BG, padx=20, pady=10)
        pub_f.pack(fill=tk.X, padx=30, pady=5)
        row1 = tk.Frame(pub_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=5)
        tk.Label(row1, text="标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row1, width=40)
        title_e.pack(side='left', padx=5)
        tk.Label(row1, text="优先级：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=(15, 5))
        pri_var = tk.StringVar(value='普通')
        ttk.Combobox(row1, textvariable=pri_var, values=['紧急', '重要', '普通'], font=("Arial", 11),
                      width=8, state='readonly').pack(side='left')
        tk.Label(pub_f, text="内容：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5, pady=(5, 0))
        content_e = scrolledtext.ScrolledText(pub_f, font=("Arial", 11), height=4, wrap='word')
        content_e.pack(fill='x', padx=5, pady=5)

        def publish():
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写公告标题和内容')
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute("INSERT INTO announcements (title,content,publisher,priority,created_at) VALUES (?,?,?,?,?)",
                         (title, content, self.user['name'], pri_var.get(), now_str()))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '发布公告', f'发布公告《{title}》')
            messagebox.showinfo('成功', '公告发布成功')
            title_e.delete(0, 'end')
            content_e.delete('1.0', 'end')
            load_ann()

        make_flat_button(pub_f, '📢 发布公告', publish, COLOR_WARNING, COLOR_WARNING, width=14).pack(pady=8)

        list_f = tk.LabelFrame(self.main_frame, text="已发布公告", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '标题', '优先级', '发布人', '发布时间')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '标题': 320, '优先级': 90, '发布人': 100, '发布时间': 150}, height=8)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load_ann():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM announcements ORDER BY id DESC").fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], r['priority'], r['publisher'], r['created_at']))

        load_ann()

        def delete_ann():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择要删除的公告')
                return
            aid = tree.item(sel[0])['values'][0]
            if messagebox.askyesno('确认', '确定删除该公告吗？'):
                conn = DB.get_conn(GOV_DB)
                conn.execute("DELETE FROM announcements WHERE id=?", (aid,))
                conn.commit()
                conn.close()
                load_ann()

        make_flat_button(list_f, '🗑️ 删除公告', delete_ann, COLOR_DANGER, COLOR_DANGER_HOVER, width=12, height=1).pack(anchor='e', padx=10, pady=5)

    # ========== 管理员：举报处理 ==========
    def _show_reports(self):
        self._clear_main()
        make_page_title(self.main_frame, "🚨 举报处理").pack(pady=20)

        filter_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        filter_f.pack(pady=5)
        tk.Label(filter_f, text="状态：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        st_var = tk.StringVar(value='待处理')
        ttk.Combobox(filter_f, textvariable=st_var, values=['全部', '待处理', '处理中', '已处理', '已驳回'],
                      font=("Arial", 11), width=10, state='readonly').pack(side='left', padx=5)

        list_frame = tk.LabelFrame(self.main_frame, text="举报列表", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '标题', '举报人', '被举报官员', '状态', '处理人', '举报时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '标题': 220, '举报人': 90, '被举报官员': 100,
                                                '状态': 80, '处理人': 80, '举报时间': 140}, height=12)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            st = st_var.get()
            if st == '全部':
                rows = conn.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
            else:
                rows = conn.execute("SELECT * FROM reports WHERE status=? ORDER BY id DESC", (st,)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], r['reporter_name'], r['official_name'],
                                                r['status'], r['handler'] or '未处理', r['created_at']))

        load()
        st_var.trace_add('write', lambda *a: load())

        def handle_report():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条举报')
                return
            rid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'处理举报 - {r["title"]}')
            win.geometry('600x550')
            win.transient(self.root)
            win.grab_set()
            info = [('举报标题', r['title']), ('举报人', r['reporter_name']), ('被举报官员', r['official_name']),
                    ('当前状态', r['status']), ('举报时间', r['created_at'])]
            for i, (k, v) in enumerate(info):
                tk.Label(win, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e').grid(row=i, column=0, sticky='e', padx=10, pady=4)
                tk.Label(win, text=str(v), font=("Arial", 10), anchor='w').grid(row=i, column=1, sticky='w', pady=4)
            tk.Label(win, text='举报内容：', font=("Arial", 11, "bold")).grid(row=5, column=0, sticky='nw', padx=10, pady=5)
            ct = scrolledtext.ScrolledText(win, font=("Arial", 11), height=8, wrap='word', width=55)
            ct.grid(row=5, column=1, pady=5, padx=5)
            ct.insert('1.0', r['content'])
            ct.config(state='disabled')
            tk.Label(win, text='处理结果：', font=("Arial", 11, "bold")).grid(row=6, column=0, sticky='nw', padx=10, pady=5)
            result_e = scrolledtext.ScrolledText(win, font=("Arial", 11), height=5, wrap='word', width=55)
            result_e.grid(row=6, column=1, pady=5, padx=5)
            if r['handle_result']:
                result_e.insert('1.0', r['handle_result'])

            def do_handle(new_status):
                result = result_e.get('1.0', 'end').strip()
                if not result:
                    messagebox.showwarning('提示', '请填写处理结果')
                    return
                conn = DB.get_conn(GOV_DB)
                conn.execute('''UPDATE reports SET status=?, handler=?, handle_result=?, handled_at=? WHERE id=?''',
                             (new_status, self.user['name'], result, now_str(), rid))
                conn.commit()
                conn.close()
                DB.log('admin', self.user['id'], self.user['name'], '处理举报', f'举报《{r["title"]}》处理结果：{new_status}')
                DB.send_message('citizen', r['reporter_id'], '举报处理通知',
                                f'您的举报《{r["title"]}》已处理（{new_status}）。\n{result}')
                messagebox.showinfo('成功', '举报已处理')
                win.destroy()
                load()

            btn_f = tk.Frame(win)
            btn_f.grid(row=7, column=0, columnspan=2, pady=15)
            if r['status'] == '待处理':
                make_flat_button(btn_f, '🔄 标记处理中', lambda: do_handle('处理中'), COLOR_WARNING, COLOR_WARNING, width=12, height=1).pack(side='left', padx=8)
            make_flat_button(btn_f, '✅ 完成处理', lambda: do_handle('已处理'), COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12, height=1).pack(side='left', padx=8)
            make_flat_button(btn_f, '❌ 驳回举报', lambda: do_handle('已驳回'), COLOR_DANGER, COLOR_DANGER_HOVER, width=12, height=1).pack(side='left', padx=8)

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '⚙️ 处理举报', handle_report, COLOR_WARNING, COLOR_WARNING, width=12, height=1).pack(side='left', padx=5)

    # ========== 管理员：起诉官员（对接法院系统） ==========
    def _show_lawsuit(self):
        self._clear_main()
        make_page_title(self.main_frame, "⚖️ 起诉官员（对接法院系统）").pack(pady=20)

        main_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        main_f.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        # 左侧：起诉表单
        left = tk.LabelFrame(main_f, text="提起公诉", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=15, pady=10)
        left.pack(side='left', fill='both', expand=True, padx=(0, 5))

        conn = DB.get_conn(GOV_DB)
        officials = conn.execute("SELECT id,name,id_number,department,position FROM officials WHERE status='在职' ORDER BY name").fetchall()
        conn.close()
        off_names = [f"{o['name']}（{o['department']}{o['position']}）" for o in officials]

        tk.Label(left, text="被起诉官员：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=0, column=0, sticky='e', padx=5, pady=8)
        off_var = tk.StringVar()
        ttk.Combobox(left, textvariable=off_var, values=off_names, font=("Arial", 11), width=28, state='readonly').grid(row=0, column=1, pady=8, padx=5)

        tk.Label(left, text="案件类型：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=1, column=0, sticky='e', padx=5, pady=8)
        case_type_var = tk.StringVar(value='职务犯罪案件')
        ttk.Combobox(left, textvariable=case_type_var, values=['职务犯罪案件', '行政案件', '民事案件', '刑事案件'],
                      font=("Arial", 11), width=26, state='readonly').grid(row=1, column=1, pady=8, padx=5)

        tk.Label(left, text="案件标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=2, column=0, sticky='e', padx=5, pady=8)
        title_e = make_groove_entry(left, width=30)
        title_e.grid(row=2, column=1, pady=8, padx=5)

        tk.Label(left, text="罪名/指控：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=3, column=0, sticky='e', padx=5, pady=8)
        charge_e = make_groove_entry(left, width=30)
        charge_e.grid(row=3, column=1, pady=8, padx=5)

        tk.Label(left, text="案情描述：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=4, column=0, sticky='ne', padx=5, pady=8)
        desc_e = scrolledtext.ScrolledText(left, font=("Arial", 11), height=6, width=30, wrap='word')
        desc_e.grid(row=4, column=1, pady=8, padx=5)

        tk.Label(left, text="诉讼请求：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=5, column=0, sticky='ne', padx=5, pady=8)
        req_e = scrolledtext.ScrolledText(left, font=("Arial", 11), height=4, width=30, wrap='word')
        req_e.grid(row=5, column=1, pady=8, padx=5)

        def file_lawsuit():
            if not off_var.get():
                messagebox.showwarning('提示', '请选择被起诉官员')
                return
            if not title_e.get().strip() or not charge_e.get().strip():
                messagebox.showwarning('提示', '请填写案件标题和罪名')
                return
            idx = off_names.index(off_var.get())
            off = officials[idx]
            case_number, court_case_id = DB.create_court_case(
                '蛋国人民政府', 'GOV001', off['name'], off['id_number'],
                title_e.get().strip(), case_type_var.get(), desc_e.get('1.0', 'end').strip(),
                req_e.get('1.0', 'end').strip()
            )
            if not case_number:
                return
            conn = DB.get_conn(GOV_DB)
            conn.execute('''INSERT INTO lawsuits (case_number,official_id,official_name,charge,description,
                status,court_case_id,created_at,created_by) VALUES (?,?,?,?,?,?,?,?,?)''',
                (case_number, off['id_number'], off['name'], charge_e.get().strip(),
                 desc_e.get('1.0', 'end').strip(), '已立案', court_case_id,
                 now_str(), self.user['name']))
            conn.execute("UPDATE officials SET status='停职', last_update=? WHERE id_number=?",
                         (now_str(), off['id_number']))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '起诉官员',
                   f'起诉官员{off["name"]}，罪名{charge_e.get().strip()}，案号{case_number}')
            messagebox.showinfo('立案成功', f'已对接法院系统成功立案！\n\n案号：{case_number}\n被告：{off["name"]}\n罪名：{charge_e.get().strip()}\n\n该官员已被停职，案件将在法院系统中审理。')
            title_e.delete(0, 'end')
            charge_e.delete(0, 'end')
            desc_e.delete('1.0', 'end')
            req_e.delete('1.0', 'end')
            off_var.set('')
            self._load_lawsuit_list()

        make_flat_button(left, '⚖️ 向法院提起公诉', file_lawsuit, COLOR_PURPLE, COLOR_PURPLE, width=18).grid(row=6, column=0, columnspan=2, pady=15)

        # 右侧：起诉记录
        right = tk.LabelFrame(main_f, text="起诉记录", font=("Arial", 12, "bold"),
                              bg=COLOR_CONTENT_BG, padx=10, pady=10)
        right.pack(side='left', fill='both', expand=True, padx=(5, 0))
        cols = ('id', '案号', '官员', '罪名', '状态', '立案时间')
        lawsuit_frame, self.lawsuit_tree = make_tree(right, cols, {'id': 40, '案号': 150, '官员': 90,
                                                        '罪名': 100, '状态': 80, '立案时间': 120}, height=12)
        lawsuit_frame.pack(fill=tk.BOTH, expand=True)
        self._load_lawsuit_list()

    def _load_lawsuit_list(self):
        if not hasattr(self, 'lawsuit_tree'):
            return
        for item in self.lawsuit_tree.get_children():
            self.lawsuit_tree.delete(item)
        conn = DB.get_conn(GOV_DB)
        rows = conn.execute("SELECT * FROM lawsuits ORDER BY id DESC").fetchall()
        conn.close()
        for r in rows:
            self.lawsuit_tree.insert('', 'end', values=(r['id'], r['case_number'], r['official_name'],
                                                          r['charge'], r['status'], r['created_at']))

    # ========== 管理员：咨询管理 ==========
    def _show_consult_admin(self):
        self._clear_main()
        make_page_title(self.main_frame, "💬 咨询管理").pack(pady=20)

        list_f = tk.LabelFrame(self.main_frame, text="全部咨询", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '标题', '咨询人', '分类', '受理部门', '状态', '提交时间')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '标题': 240, '咨询人': 90, '分类': 90,
                                            '受理部门': 80, '状态': 80, '提交时间': 140}, height=13)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM consultations ORDER BY id DESC").fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], r['citizen_name'], r['category'],
                                                r['department'], r['status'], r['created_at']))

        load()

        def handle_consult():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条咨询')
                return
            sid = tree.item(sel[0])['values'][0]
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM consultations WHERE id=?", (sid,)).fetchone()
            conn.close()
            win = tk.Toplevel(self.root)
            win.title(f'咨询详情 - {r["title"]}')
            win.geometry('560x500')
            win.transient(self.root)
            win.grab_set()
            info = [('咨询标题', r['title']), ('咨询人', r['citizen_name']), ('分类', r['category']),
                    ('受理部门', r['department']), ('状态', r['status']), ('提交时间', r['created_at'])]
            for i, (k, v) in enumerate(info):
                tk.Label(win, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e').grid(row=i, column=0, sticky='e', padx=10, pady=4)
                tk.Label(win, text=str(v), font=("Arial", 10), anchor='w').grid(row=i, column=1, sticky='w', pady=4)
            tk.Label(win, text='咨询内容：', font=("Arial", 11, "bold")).grid(row=6, column=0, sticky='nw', padx=10, pady=5)
            ct = scrolledtext.ScrolledText(win, font=("Arial", 11), height=6, wrap='word', width=50)
            ct.grid(row=6, column=1, pady=5, padx=5)
            ct.insert('1.0', r['content'])
            ct.config(state='disabled')
            tk.Label(win, text='回复内容：', font=("Arial", 11, "bold")).grid(row=7, column=0, sticky='nw', padx=10, pady=5)
            reply_e = scrolledtext.ScrolledText(win, font=("Arial", 11), height=5, wrap='word', width=50)
            reply_e.grid(row=7, column=1, pady=5, padx=5)
            if r['reply']:
                reply_e.insert('1.0', r['reply'])

            def do_reply():
                reply_text = reply_e.get('1.0', 'end').strip()
                if not reply_text:
                    messagebox.showwarning('提示', '请填写回复内容')
                    return
                conn = DB.get_conn(GOV_DB)
                conn.execute('''UPDATE consultations SET status='已回复', reply=?, replier=?, replied_at=? WHERE id=?''',
                             (reply_text, self.user['name'], now_str(), sid))
                conn.commit()
                conn.close()
                DB.log('admin', self.user['id'], self.user['name'], '回复咨询', f'回复咨询《{r["title"]}》')
                DB.send_message('citizen', r['citizen_id'], '咨询回复通知',
                                f'您的咨询《{r["title"]}》已获回复。\n{reply_text}')
                messagebox.showinfo('成功', '回复已提交')
                win.destroy()
                load()

            make_flat_button(win, '✅ 提交回复', do_reply, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=14).grid(row=8, column=0, columnspan=2, pady=15)

        def delete_consult():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条咨询')
                return
            sid = tree.item(sel[0])['values'][0]
            if messagebox.askyesno('确认', '确定删除该咨询吗？'):
                conn = DB.get_conn(GOV_DB)
                conn.execute("DELETE FROM consultations WHERE id=?", (sid,))
                conn.commit()
                conn.close()
                load()

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=10)
        make_flat_button(btn_f, '💬 查看/回复', handle_consult, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12, height=1).pack(side='left', padx=5)
        make_flat_button(btn_f, '🗑️ 删除咨询', delete_consult, COLOR_DANGER, COLOR_DANGER_HOVER, width=12, height=1).pack(side='left', padx=5)

    # ========== 管理员：消息管理（广播） ==========
    # ========== 第 5 批：漏洞反馈 / 抽奖 / 权力暂时交接 / 单人候选 ==========
    def _pick_image_b64(self, max_w=900):
        """选一张 jpg/png 图片，压缩后转 base64（存数据库）。"""
        p = filedialog.askopenfilename(parent=self.root, title='选择图片（jpg / png）',
                                       filetypes=[('图片文件', '*.jpg *.jpeg *.png'), ('所有文件', '*.*')])
        if not p:
            return ''
        ext = os.path.splitext(p)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png'):
            messagebox.showwarning('提示', '只支持 jpg / png 图片')
            return ''
        try:
            with Image.open(p) as im:
                im = im.convert('RGB')
                if im.width > max_w:
                    im = im.resize((max_w, max(1, int(im.height * max_w / im.width))))
                buf = io.BytesIO()
                im.save(buf, format='PNG')
            return base64.b64encode(buf.getvalue()).decode('ascii')
        except Exception as e:
            messagebox.showerror('读取失败', str(e))
            return ''

    def _img_from_b64(self, b64, max_w=520, max_h=240):
        """base64 → 屏幕用的 PhotoImage（调用方需保持引用）。"""
        try:
            if not b64:
                return None
            data = b64.split(',', 1)[1] if b64.startswith('b64:') else b64
            rawb = base64.b64decode(data)
            with Image.open(io.BytesIO(rawb)) as im:
                im = im.convert('RGB')
                w, h = im.size
                s = min(float(max_w) / w, float(max_h) / h, 1.0)
                im = im.resize((max(1, int(w * s)), max(1, int(h * s)))
                               if s < 1.0 else (w, h))
                return ImageTk.PhotoImage(im)
        except Exception:
            return None

    # ---------- 漏洞反馈（提交） ----------
    def _show_bug_report(self):
        self._clear_main()
        make_page_title(self.main_frame, '🐞 反馈漏洞').pack(pady=14)
        tk.Label(self.main_frame,
                 text='发现系统问题或有改进建议，请在这里提交；处理人：袁泓涵 / 系统管理员，处理结果会回复给你。',
                 font=('Arial', 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(pady=(0, 6))
        form = tk.LabelFrame(self.main_frame, text='提交漏洞 / 建议', font=('Arial', 12, 'bold'),
                             bg=COLOR_CONTENT_BG, padx=16, pady=8)
        form.pack(fill='x', padx=30, pady=4)
        r1 = tk.Frame(form, bg=COLOR_CONTENT_BG)
        r1.pack(fill='x', pady=3)
        tk.Label(r1, text='问题标题：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left')
        title_e = make_groove_entry(r1, width=52)
        title_e.pack(side='left', padx=4)
        tk.Label(form, text='详细描述（操作步骤、报错信息等）：', bg=COLOR_CONTENT_BG,
                 font=('Arial', 11)).pack(anchor='w', pady=(4, 0))
        content_e = scrolledtext.ScrolledText(form, height=5, font=('Arial', 11), wrap='word')
        content_e.pack(fill='x', pady=3)
        img_state = {'b64': ''}
        img_row = tk.Frame(form, bg=COLOR_CONTENT_BG)
        img_row.pack(fill='x', pady=2)
        img_lbl = tk.Label(img_row, text='截图（可选，jpg/png）：未上传', bg=COLOR_CONTENT_BG,
                           font=('Arial', 9), fg=COLOR_TEXT_GRAY)
        img_lbl.pack(side='left', padx=(0, 8))

        def pick():
            b64 = self._pick_image_b64(720)
            if b64:
                img_state['b64'] = b64
                img_lbl.config(text='截图已选择（%d KB）' % (len(b64) // 1024 or 1), fg=COLOR_SUCCESS)

        make_flat_button(img_row, '📎 选择截图', pick, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(side='left')

        def submit():
            ok, msg = DB.submit_bug_report(self.user, title_e.get().strip(),
                                           content_e.get('1.0', 'end').strip(), img_state['b64'])
            messagebox.showinfo('提示', msg)
            if ok:
                self._show_bug_report()

        make_flat_button(form, '🐞 提交反馈', submit, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                         width=14, height=1).pack(anchor='e', pady=6)

        lst = tk.LabelFrame(self.main_frame, text='我提交的反馈（双击查看详情）', font=('Arial', 12, 'bold'),
                            bg=COLOR_CONTENT_BG, padx=10, pady=6)
        lst.pack(fill='both', expand=True, padx=30, pady=8)
        fr, tree = make_tree(lst, ['ID', '标题', '状态', '处理人', '回复', '提交时间'],
                             {'ID': 50, '标题': 240, '状态': 80, '处理人': 110, '回复': 280, '提交时间': 140},
                             height=8)
        fr.pack(fill='both', expand=True)
        rows = {}
        for r in DB.my_bug_reports(self.user):
            iid = tree.insert('', 'end', values=(r['id'], r['title'], r['status'],
                                                 r['handled_by'] or '—',
                                                 (r['reply'] or '—')[:30], r['created_at'][:16]))
            rows[iid] = r

        def detail(_e=None):
            s = tree.selection()
            if not s:
                return
            r = rows[s[0]]
            win = tk.Toplevel(self.root)
            win.title('反馈详情 #%s' % r['id'])
            win.geometry('600x500')
            win.configure(bg='white')
            tk.Label(win, text=r['title'], font=('Arial', 14, 'bold'), bg='white',
                     wraplength=540, justify='left').pack(pady=(14, 4), padx=20)
            tk.Label(win, text='状态：%s    处理人：%s    提交：%s' % (
                r['status'], r['handled_by'] or '—', r['created_at'][:16]),
                font=('Arial', 10), bg='white', fg=COLOR_TEXT_GRAY).pack(anchor='w', padx=20)
            tk.Label(win, text='问题描述', font=('Arial', 11, 'bold'), bg='white').pack(anchor='w', padx=20, pady=(8, 2))
            t = tk.Text(win, height=5, font=('Arial', 11), wrap='word', bd=1, relief=tk.SOLID)
            t.insert('1.0', r['content'] or '')
            t.config(state='disabled')
            t.pack(fill='x', padx=20)
            ph = self._img_from_b64(r.get('image_b64'), 520, 200)
            if ph:
                lb = tk.Label(win, image=ph, bg='white')
                lb.image = ph
                lb.pack(pady=6)
            tk.Label(win, text='处理回复', font=('Arial', 11, 'bold'), bg='white').pack(anchor='w', padx=20, pady=(8, 2))
            t2 = tk.Text(win, height=4, font=('Arial', 11), wrap='word', bd=1, relief=tk.SOLID)
            t2.insert('1.0', r['reply'] or '（暂未回复）')
            t2.config(state='disabled')
            t2.pack(fill='x', padx=20)
            make_flat_button(win, '关闭', win.destroy, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=10, height=1).pack(pady=10)
            win.transient(self.root)

        tree.bind('<Double-1>', detail)

    # ---------- 漏洞处理（袁泓涵 / 管理员） ----------
    def _show_bug_admin(self):
        if not DB.is_super_user(self.user):
            messagebox.showwarning('提示', '只有袁泓涵与系统管理员可以处理漏洞反馈')
            return
        self._clear_main()
        make_page_title(self.main_frame, '🐞 漏洞处理').pack(pady=14)
        bar = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        bar.pack(pady=4)
        st = tk.StringVar(value='全部')
        tk.Label(bar, text='状态：', bg=COLOR_CONTENT_BG, font=('Arial', 10)).pack(side='left')
        ttk.Combobox(bar, textvariable=st, values=['全部', '待处理', '已处理'], width=10,
                     state='readonly', font=('Arial', 10)).pack(side='left', padx=6)
        lst = tk.LabelFrame(self.main_frame, text='反馈列表（双击查看 / 回复）', font=('Arial', 12, 'bold'),
                            bg=COLOR_CONTENT_BG, padx=10, pady=6)
        lst.pack(fill='both', expand=True, padx=25, pady=6)
        fr, tree = make_tree(lst, ['ID', '提交人', 'ID号', '标题', '状态', '时间'],
                             {'ID': 50, '提交人': 100, 'ID号': 150, '标题': 300, '状态': 80, '时间': 140},
                             height=10)
        fr.pack(fill='both', expand=True)
        rows = {}

        def load(_e=None):
            for it in tree.get_children():
                tree.delete(it)
            rows.clear()
            data = DB.list_bug_reports(None if st.get() == '全部' else st.get())
            for r in data:
                iid = tree.insert('', 'end', values=(r['id'], r['reporter_name'], r['id_number'],
                                                     r['title'], r['status'], r['created_at'][:16]))
                rows[iid] = r

        load()
        st.trace_add('write', lambda *_: load())

        def open_detail(_e=None):
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请先选择一条反馈')
                return
            r = rows[s[0]]
            win = tk.Toplevel(self.root)
            win.title('漏洞详情 #%s' % r['id'])
            win.geometry('620x560')
            win.configure(bg='white')
            tk.Label(win, text=r['title'], font=('Arial', 14, 'bold'), bg='white',
                     wraplength=560, justify='left').pack(pady=(14, 4), padx=20)
            tk.Label(win, text='提交人：%s（%s，%s）    时间：%s' % (
                r['reporter_name'], r['reporter_type'], r['id_number'], r['created_at'][:16]),
                font=('Arial', 10), bg='white', fg=COLOR_TEXT_GRAY).pack(anchor='w', padx=20)
            t = tk.Text(win, height=6, font=('Arial', 11), wrap='word', bd=1, relief=tk.SOLID)
            t.insert('1.0', r['content'] or '')
            t.config(state='disabled')
            t.pack(fill='x', padx=20, pady=6)
            ph = self._img_from_b64(r.get('image_b64'), 540, 200)
            if ph:
                lb = tk.Label(win, image=ph, bg='white')
                lb.image = ph
                lb.pack(pady=4)
            tk.Label(win, text='回复内容：', font=('Arial', 11, 'bold'), bg='white').pack(anchor='w', padx=20)
            rep_e = scrolledtext.ScrolledText(win, height=5, font=('Arial', 11), wrap='word')
            rep_e.insert('1.0', r['reply'] or '')
            rep_e.pack(fill='x', padx=20, pady=4)

            def do_reply():
                ok, msg = DB.reply_bug_report(r['id'], rep_e.get('1.0', 'end').strip(),
                                              self.user.get('name') or '')
                messagebox.showinfo('提示', msg)
                if ok:
                    win.destroy()
                    load()

            make_flat_button(win, '✅ 回复并标记已处理', do_reply, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                             width=18, height=1).pack(pady=10)
            win.transient(self.root)

        tree.bind('<Double-1>', open_detail)
        make_flat_button(lst, '👁️ 查看 / 回复', open_detail, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=14, height=1).pack(pady=6)

    # ---------- 抽奖活动（参与） ----------
    def _show_lottery_view(self):
        self._clear_main()
        make_page_title(self.main_frame, '🎁 抽奖活动').pack(pady=14)
        drawn = DB.draw_all_due_lotteries()
        tip = '到点自动开奖：中奖者会收到消息，并需填写姓名、电子邮箱等资料。'
        if drawn:
            tip = '刚刚开奖：' + '；'.join('%s（%s）' % (t, m) for t, m in drawn) + chr(10) + tip
        tk.Label(self.main_frame, text=tip, font=('Arial', 10), bg=COLOR_CONTENT_BG,
                 fg=COLOR_PURPLE, justify='left', wraplength=900).pack(pady=(0, 6))

        lst = tk.LabelFrame(self.main_frame, text='进行中的抽奖（双击查看详情）', font=('Arial', 12, 'bold'),
                            bg=COLOR_CONTENT_BG, padx=10, pady=6)
        lst.pack(fill='both', expand=True, padx=25, pady=4)
        fr, tree = make_tree(lst, ['ID', '名称', '奖品', '开奖时间', '状态', '参与人数'],
                             {'ID': 50, '名称': 240, '奖品': 180, '开奖时间': 150, '状态': 80, '参与人数': 80},
                             height=7)
        fr.pack(fill='both', expand=True)
        rows = {}
        for lt in DB.list_lotteries():
            n = len(DB.lottery_entries(lt['id'])) if lt['status'] == '报名中' else '-'
            iid = tree.insert('', 'end', values=(lt['id'], lt['title'], lt['prize'] or '—',
                                                 (lt['draw_at'] or '')[:16], lt['status'], n))
            rows[iid] = lt

        def selected():
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请先选择一条抽奖')
                return None
            return rows[s[0]]

        def say_detail():
            lt = selected()
            if not lt:
                return
            win = tk.Toplevel(self.root)
            win.title('抽奖详情')
            win.geometry('600x520')
            win.configure(bg='white')
            tk.Label(win, text='🎁 ' + (lt['title'] or ''), font=('Arial', 15, 'bold'),
                     bg='white', fg=COLOR_PURPLE).pack(pady=(14, 4), padx=20)
            tk.Label(win, text='奖品：%s    开奖时间：%s    中奖人数：%s' % (
                lt['prize'] or '—', (lt['draw_at'] or '')[:16], lt['winner_count']),
                font=('Arial', 10), bg='white', fg=COLOR_TEXT_GRAY).pack()
            ph = self._img_from_b64(lt.get('image_b64'), 540, 220)
            if ph:
                lb = tk.Label(win, image=ph, bg='white')
                lb.image = ph
                lb.pack(pady=6)
            t = tk.Text(win, height=8, font=('Arial', 11), wrap='word', bd=1, relief=tk.SOLID)
            t.insert('1.0', lt['content'] or '（无详细说明）')
            t.config(state='disabled')
            t.pack(fill='both', expand=True, padx=20, pady=6)

            def join():
                ok, msg = DB.join_lottery(lt['id'], self.user)
                messagebox.showinfo('提示', msg)
                if ok:
                    win.destroy()
                    self._show_lottery_view()

            make_flat_button(win, '🎯 我要参与抽奖', join, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                             width=16, height=1).pack(pady=10)
            win.transient(self.root)

        tree.bind('<Double-1>', lambda _e: say_detail())

        win_f = tk.LabelFrame(self.main_frame, text='🏆 我的中奖记录（中奖后请填写资料）',
                              font=('Arial', 12, 'bold'), bg=COLOR_CONTENT_BG, padx=10, pady=6)
        win_f.pack(fill='x', padx=25, pady=8)
        my = DB.my_winner_entries(self.user)
        if not my:
            tk.Label(win_f, text='暂无中奖记录', bg=COLOR_CONTENT_BG, font=('Arial', 10),
                     fg=COLOR_TEXT_GRAY).pack(anchor='w')
        for e in my:
            r = tk.Frame(win_f, bg=COLOR_CONTENT_BG)
            r.pack(fill='x', pady=2)
            txt = '%s｜奖品：%s｜%s' % (e.get('lottery_title') or '', e.get('lottery_prize') or '—',
                                       '资料已填写' if e.get('filled') else '待填写资料')
            tk.Label(r, text=txt, bg=COLOR_CONTENT_BG, font=('Arial', 10)).pack(side='left')

            def fill(_id=e['id'], _t=e.get('lottery_title') or ''):
                w2 = tk.Toplevel(self.root)
                w2.title('填写领奖资料')
                w2.geometry('420x300')
                w2.configure(bg='white')
                tk.Label(w2, text='中奖：' + _t, font=('Arial', 12, 'bold'), bg='white',
                         fg=COLOR_SUCCESS).pack(pady=10)
                fr2 = tk.Frame(w2, bg='white')
                fr2.pack(padx=20, fill='x')
                vals = {}
                for i, (k, key) in enumerate([('姓名*', 'name'), ('电子邮箱*', 'email'), ('手机号', 'phone')]):
                    tk.Label(fr2, text=k, bg='white', font=('Arial', 10)).grid(row=i, column=0, sticky='w', pady=4)
                    e2 = make_groove_entry(fr2, width=26)
                    e2.grid(row=i, column=1, pady=4, padx=6)
                    vals[key] = e2

                def save2():
                    ok, msg = DB.fill_winner_info(_id, vals['name'].get(), vals['email'].get(),
                                                  vals['phone'].get())
                    messagebox.showinfo('提示', msg)
                    if ok:
                        w2.destroy()
                        self._show_lottery_view()

                make_flat_button(w2, '提交资料', save2, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                                 width=12, height=1).pack(pady=10)
                w2.transient(self.root)

            make_flat_button(r, '✍️ 填写资料', fill, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=10, height=1).pack(side='left', padx=8)

    # ---------- 抽奖管理 ----------
    def _show_lottery_admin(self):
        if not DB.is_super_user(self.user):
            messagebox.showwarning('提示', '只有袁泓涵 / 总统 / 系统管理员可以发布抽奖活动')
            return
        self._clear_main()
        make_page_title(self.main_frame, '🎁 抽奖管理').pack(pady=14)
        form = tk.LabelFrame(self.main_frame, text='发布抽奖活动', font=('Arial', 12, 'bold'),
                             bg=COLOR_CONTENT_BG, padx=16, pady=8)
        form.pack(fill='x', padx=25, pady=4)
        r1 = tk.Frame(form, bg=COLOR_CONTENT_BG)
        r1.pack(fill='x', pady=3)
        tk.Label(r1, text='抽奖名称：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left')
        name_e = make_groove_entry(r1, width=30)
        name_e.pack(side='left', padx=4)
        tk.Label(r1, text='奖品：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left', padx=(12, 2))
        prize_e = make_groove_entry(r1, width=22)
        prize_e.pack(side='left', padx=4)
        r2 = tk.Frame(form, bg=COLOR_CONTENT_BG)
        r2.pack(fill='x', pady=3)
        tk.Label(r2, text='开奖时间：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left')
        time_e = make_groove_entry(r2, width=18)
        time_e.pack(side='left', padx=4)
        tk.Label(r2, text='格式 2026-12-31 20:00', bg=COLOR_CONTENT_BG,
                 font=('Arial', 9), fg=COLOR_TEXT_GRAY).pack(side='left')
        tk.Label(r2, text='中奖人数：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left', padx=(12, 2))
        cnt_e = make_groove_entry(r2, width=8)
        cnt_e.insert(0, '1')
        cnt_e.pack(side='left', padx=4)
        tk.Label(form, text='活动说明：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(anchor='w', pady=(4, 0))
        desc_e = scrolledtext.ScrolledText(form, height=4, font=('Arial', 11), wrap='word')
        desc_e.pack(fill='x', pady=3)
        img_state = {'b64': ''}
        ir = tk.Frame(form, bg=COLOR_CONTENT_BG)
        ir.pack(fill='x', pady=2)
        il = tk.Label(ir, text='活动图片（jpg/png，可选）：未上传', bg=COLOR_CONTENT_BG,
                      font=('Arial', 9), fg=COLOR_TEXT_GRAY)
        il.pack(side='left', padx=(0, 8))

        def pick():
            b64 = self._pick_image_b64(800)
            if b64:
                img_state['b64'] = b64
                il.config(text='图片已选择', fg=COLOR_SUCCESS)

        make_flat_button(ir, '📎 选择图片', pick, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(side='left')

        def publish():
            t = self._norm_dt(time_e.get())
            if time_e.get().strip() and not t:
                messagebox.showwarning('提示', '开奖时间格式不对，应为 2026-12-31 20:00')
                return
            ok, msg = DB.publish_lottery(name_e.get().strip(), desc_e.get('1.0', 'end').strip(),
                                         prize_e.get().strip(), time_e.get().strip(),
                                         cnt_e.get().strip() or '1', self.user.get('name') or '',
                                         img_state['b64'])
            messagebox.showinfo('提示', msg)
            if ok:
                self._show_lottery_admin()

        make_flat_button(form, '🎁 发布抽奖', publish, COLOR_WARNING, COLOR_WARNING,
                         width=14, height=1).pack(anchor='e', pady=6)

        lst = tk.LabelFrame(self.main_frame, text='已发布抽奖', font=('Arial', 12, 'bold'),
                            bg=COLOR_CONTENT_BG, padx=10, pady=6)
        lst.pack(fill='both', expand=True, padx=25, pady=6)
        fr, tree = make_tree(lst, ['ID', '名称', '奖品', '开奖时间', '中奖人数', '状态', '参与'],
                             {'ID': 50, '名称': 220, '奖品': 150, '开奖时间': 150, '中奖人数': 80,
                              '状态': 80, '参与': 70}, height=7)
        fr.pack(fill='both', expand=True)
        rows = {}
        for lt in DB.list_lotteries():
            n = len(DB.lottery_entries(lt['id']))
            iid = tree.insert('', 'end', values=(lt['id'], lt['title'], lt['prize'] or '—',
                                                 (lt['draw_at'] or '')[:16], lt['winner_count'],
                                                 lt['status'], n))
            rows[iid] = lt

        def sel():
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请先选择一条抽奖')
                return None
            return rows[s[0]]

        def do_draw():
            lt = sel()
            if not lt:
                return
            if not messagebox.askyesno('确认', '立即对「%s」开奖？' % lt['title']):
                return
            ok, msg = DB.draw_lottery_if_due(lt['id'])
            messagebox.showinfo('提示', msg)
            self._show_lottery_admin()

        def do_view():
            lt = sel()
            if not lt:
                return
            win = tk.Toplevel(self.root)
            win.title('参与名单 - %s' % (lt['title'] or ''))
            win.geometry('640x420')
            win.configure(bg='white')
            ents = DB.lottery_entries(lt['id'])
            tk.Label(win, text='参与者 %d 人（★=中奖）' % len(ents), bg='white',
                     font=('Arial', 12, 'bold')).pack(pady=8)
            fr2, tr2 = make_tree(win, ['参与人', '类型', '是否中奖', '姓名', '邮箱', '手机'],
                                 {'参与人': 110, '类型': 70, '是否中奖': 80, '姓名': 90, '邮箱': 150, '手机': 100},
                                 height=12)
            fr2.pack(fill='both', expand=True, padx=12, pady=6)
            for e in ents:
                tr2.insert('', 'end', values=(e['participant_name'], e['participant_type'],
                                              '★ 中奖' if e['is_winner'] else '—',
                                              e['real_name'] or '—', e['email'] or '—', e['phone'] or '—'))
            win.transient(self.root)

        def do_delete():
            lt = sel()
            if lt and messagebox.askyesno('确认', '删除抽奖「%s」及其报名记录？' % lt['title']):
                ok, msg = DB.delete_lottery(lt['id'])
                messagebox.showinfo('提示', msg)
                self._show_lottery_admin()

        bf = tk.Frame(lst, bg=COLOR_CONTENT_BG)
        bf.pack(pady=6)
        make_flat_button(bf, '👁️ 参与名单', do_view, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12, height=1).pack(side='left', padx=4)
        make_flat_button(bf, '🎰 立即开奖', do_draw, COLOR_WARNING, '#d68910', width=12, height=1).pack(side='left', padx=4)
        make_flat_button(bf, '🗑️ 删除', do_delete, COLOR_DANGER, '#a93226', width=10, height=1).pack(side='left', padx=4)

    # ---------- 权力暂时交接 ----------
    def _show_power_transfer(self):
        if not DB.has_president_power(self.user):
            messagebox.showwarning('提示', '只有总统（含代行总统职权者）可以发起权力暂时交接')
            return
        self._clear_main()
        make_page_title(self.main_frame, '🕊️ 总统权力暂时交接').pack(pady=14)
        cur = DB.active_transfer()
        box = tk.LabelFrame(self.main_frame, text='当前状态', font=('Arial', 12, 'bold'),
                            bg=COLOR_CONTENT_BG, padx=16, pady=8)
        box.pack(fill='x', padx=30, pady=6)
        if cur:
            tk.Label(box, text='正在交接中：%s → %s（%s 至 %s）' % (
                cur['from_name'], cur['to_name'], (cur['start_at'] or '')[:16], (cur['end_at'] or '')[:16]),
                bg=COLOR_CONTENT_BG, font=('Arial', 11), fg=COLOR_SUCCESS).pack(anchor='w')

            def revoke():
                if messagebox.askyesno('确认', '提前收回总统职权？'):
                    ok, msg = DB.revoke_power_transfer(self.user.get('name') or '')
                    messagebox.showinfo('提示', msg)
                    self._show_power_transfer()

            make_flat_button(box, '↩️ 提前收回职权', revoke, COLOR_DANGER, '#a93226',
                             width=16, height=1).pack(anchor='w', pady=6)
        else:
            tk.Label(box, text='当前没有生效中的权力交接。', bg=COLOR_CONTENT_BG,
                     font=('Arial', 11), fg=COLOR_TEXT_GRAY).pack(anchor='w')
            conn = DB.get_conn(GOV_DB)
            vps = [dict(r) for r in conn.execute(
                "SELECT * FROM officials WHERE position='副总统' AND status='在职'")]
            conn.close()
            if not vps:
                tk.Label(box, text='⚠️ 当前没有在职副总统，暂不能交接（副总统须由总统大选产生）。',
                         bg=COLOR_CONTENT_BG, font=('Arial', 10), fg=COLOR_WARNING).pack(anchor='w', pady=4)
            else:
                r = tk.Frame(box, bg=COLOR_CONTENT_BG)
                r.pack(fill='x', pady=4)
                tk.Label(r, text='交给：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left')
                vname = tk.StringVar(value=vps[0]['name'])
                ttk.Combobox(r, textvariable=vname, values=[v['name'] for v in vps], width=14,
                             state='readonly', font=('Arial', 10)).pack(side='left', padx=4)
                tk.Label(r, text='收回时间：', bg=COLOR_CONTENT_BG, font=('Arial', 11)).pack(side='left', padx=(12, 2))
                end_e = make_groove_entry(r, width=17)
                end_e.pack(side='left', padx=4)
                tk.Label(r, text='2026-12-01 18:00', bg=COLOR_CONTENT_BG,
                         font=('Arial', 9), fg=COLOR_TEXT_GRAY).pack(side='left')

                def do_start():
                    t = self._norm_dt(end_e.get())
                    if end_e.get().strip() and not t:
                        messagebox.showwarning('提示', '收回时间格式不对，应为 2026-12-01 18:00')
                        return
                    vp = next((v for v in vps if v['name'] == vname.get()), None)
                    ok, msg = DB.start_power_transfer(dict(self.user), vp, t or end_e.get().strip())
                    messagebox.showinfo('提示', msg)
                    if ok:
                        self._show_power_transfer()

                make_flat_button(box, '🕊️ 发起交接', do_start, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                                 width=14, height=1).pack(anchor='w', pady=6)

        hist = tk.LabelFrame(self.main_frame, text='交接记录', font=('Arial', 12, 'bold'),
                             bg=COLOR_CONTENT_BG, padx=10, pady=6)
        hist.pack(fill='both', expand=True, padx=30, pady=6)
        fr, tree = make_tree(hist, ['ID', '交出人', '接收人', '开始', '应收回', '状态'],
                             {'ID': 50, '交出人': 110, '接收人': 110, '开始': 150, '应收回': 150, '状态': 80},
                             height=7)
        fr.pack(fill='both', expand=True)
        for t in DB.list_power_transfers():
            tree.insert('', 'end', values=(t['id'], t['from_name'], t['to_name'],
                                           (t['start_at'] or '')[:16], (t['end_at'] or '')[:16], t['status']))

    # ---------- 单人候选人名单 ----------
    def _show_solo_candidates(self):
        self._clear_main()
        make_page_title(self.main_frame, '🗳️ 单人候选人名单').pack(pady=14)
        tk.Label(self.main_frame,
                 text='单人参选登记经袁泓涵 / 系统管理员审核通过后出现在这里；其他人可申请担任其副总统搭档，'
                      '由该候选人本人审核，通过后正式参加大选。',
                 font=('Arial', 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY,
                 wraplength=900, justify='left').pack(pady=(0, 6))

        top = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        top.pack(pady=2)

        def solo_apply():
            win = tk.Toplevel(self.root)
            win.title('单人参选登记')
            win.geometry('560x420')
            win.configure(bg='white')
            tk.Label(win, text='🗳️ 单人参选登记（暂不绑定副总统搭档）', font=('Arial', 13, 'bold'),
                     bg='white', fg=COLOR_PURPLE).pack(pady=10)
            tk.Label(win, text='登记 → 袁泓涵/管理员审核 → 进入单人候选人名单 → 他人申请搭档 → 你审核通过 → 正式参选',
                     bg='white', font=('Arial', 9), fg=COLOR_TEXT_GRAY, wraplength=500).pack()
            tk.Label(win, text='竞选申请书：', bg='white', font=('Arial', 11)).pack(anchor='w', padx=20, pady=(8, 2))
            te = scrolledtext.ScrolledText(win, height=9, font=('Arial', 11), wrap='word')
            te.pack(fill='both', expand=True, padx=20, pady=4)

            def submit():
                ok, msg = DB.submit_solo_candidacy(self.user, te.get('1.0', 'end').strip())
                messagebox.showinfo('提示', msg)
                if ok:
                    win.destroy()
                    self._show_solo_candidates()

            make_flat_button(win, '📨 提交单人参选登记', submit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=18, height=1).pack(pady=10)
            win.transient(self.root)

        make_flat_button(top, '📝 我要单人参选登记', solo_apply, COLOR_PURPLE, '#7d3c98',
                         width=16, height=1).pack(side='left', padx=6)

        lst = tk.LabelFrame(self.main_frame, text='单人候选人名单（双击申请搭档）', font=('Arial', 12, 'bold'),
                            bg=COLOR_CONTENT_BG, padx=10, pady=6)
        lst.pack(fill='both', expand=True, padx=25, pady=6)
        fr, tree = make_tree(lst, ['ID', '姓名', '身份证号', '党派', '状态', '搭档', '登记时间'],
                             {'ID': 50, '姓名': 100, '身份证号': 160, '党派': 100, '状态': 90,
                              '搭档': 110, '登记时间': 140}, height=8)
        fr.pack(fill='both', expand=True)
        rows = {}
        for s in DB.list_solo_candidates():
            iid = tree.insert('', 'end', values=(s['id'], s['name'], s['id_number'],
                                                 s['party_name'] or '独立候选人', s['status'],
                                                 s['partner_name'] or '—', (s['created_at'] or '')[:16]))
            rows[iid] = s

        def apply_partner(_e=None):
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一位单人候选人')
                return
            s = rows[sel[0]]
            if s['status'] != '招募搭档':
                messagebox.showwarning('提示', '该候选人当前不接受搭档申请（状态：%s）' % s['status'])
                return
            if not messagebox.askyesno('确认', ('申请担任 %s 的副总统搭档？' % s['name']) + chr(10) +
                                       '提交后需候选人本人审核。'):
                return
            ok, msg = DB.apply_solo_partner(s['id'], self.user)
            messagebox.showinfo('提示', msg)
            if ok:
                self._show_solo_candidates()

        tree.bind('<Double-1>', apply_partner)
        make_flat_button(lst, '🤝 申请担任其副总统搭档', apply_partner, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                         width=20, height=1).pack(pady=6)

        mine = tk.LabelFrame(self.main_frame, text='我的搭档申请', font=('Arial', 12, 'bold'),
                             bg=COLOR_CONTENT_BG, padx=10, pady=6)
        mine.pack(fill='x', padx=25, pady=6)
        my_reqs = [r for r in DB.solo_partner_requests() if r['id_number'] == (self.user.get('id_number') or '')]
        if not my_reqs:
            tk.Label(mine, text='暂无申请记录', bg=COLOR_CONTENT_BG, font=('Arial', 10),
                     fg=COLOR_TEXT_GRAY).pack(anchor='w')
        for r in my_reqs:
            s = next((x for x in DB.list_solo_candidates() if x['id'] == r['solo_id']), None)
            tk.Label(mine, text='→ %s：%s' % ((s or {}).get('name') or '—', r['status']),
                     bg=COLOR_CONTENT_BG, font=('Arial', 10)).pack(anchor='w')

    # ---------- 单人候选审核 ----------
    def _show_solo_admin(self):
        if not DB.is_super_user(self.user) and '总统' not in (self.user.get('position') or ''):
            messagebox.showwarning('提示', '只有袁泓涵 / 总统 / 系统管理员可以审核单人参选登记')
            return
        self._clear_main()
        make_page_title(self.main_frame, '📝 单人参选 / 搭档申请审核').pack(pady=14)

        lst1 = tk.LabelFrame(self.main_frame, text='① 单人参选登记审核（待审核）',
                             font=('Arial', 12, 'bold'), bg=COLOR_CONTENT_BG, padx=10, pady=6)
        lst1.pack(fill='both', expand=True, padx=25, pady=4)
        fr1, tree1 = make_tree(lst1, ['ID', '姓名', '身份证号', '状态', '申请书', '时间'],
                               {'ID': 50, '姓名': 100, '身份证号': 160, '状态': 90, '申请书': 300, '时间': 140},
                               height=6)
        fr1.pack(fill='both', expand=True)
        rows1 = {}
        for s in DB.list_solo_candidates():
            plat = (s['platform'] or '').replace(chr(10), ' ')
            iid = tree1.insert('', 'end', values=(s['id'], s['name'], s['id_number'], s['status'],
                                                  plat[:40], (s['created_at'] or '')[:16]))
            rows1[iid] = s

        def pick1():
            sel = tree1.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条登记')
                return None
            return rows1[sel[0]]

        def approve1(_e=None):
            s = pick1()
            if not s:
                return
            if s['status'] != '待审核':
                messagebox.showwarning('提示', '该登记已处理过（%s）' % s['status'])
                return
            if messagebox.askyesno('确认', '通过 %s 的单人参选登记？通过后进入单人候选人名单。' % s['name']):
                ok, msg = DB.review_solo_candidacy(s['id'], True, self.user.get('name') or '')
                messagebox.showinfo('提示', msg)
                self._show_solo_admin()

        def reject1():
            s = pick1()
            if not s:
                return
            if messagebox.askyesno('确认', '拒绝 %s 的单人参选登记？' % s['name']):
                ok, msg = DB.review_solo_candidacy(s['id'], False, self.user.get('name') or '')
                messagebox.showinfo('提示', msg)
                self._show_solo_admin()

        b1 = tk.Frame(lst1, bg=COLOR_CONTENT_BG)
        b1.pack(pady=5)
        make_flat_button(b1, '✅ 通过', approve1, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=10, height=1).pack(side='left', padx=4)
        make_flat_button(b1, '❌ 拒绝', reject1, COLOR_DANGER, '#a93226', width=10, height=1).pack(side='left', padx=4)

        lst2 = tk.LabelFrame(self.main_frame,
                             text='② 搭档申请审核（仅本人是单人候选人时可见）', font=('Arial', 12, 'bold'),
                             bg=COLOR_CONTENT_BG, padx=10, pady=6)
        lst2.pack(fill='both', expand=True, padx=25, pady=6)
        my_solo = [s for s in DB.list_solo_candidates()
                   if s['id_number'] == (self.user.get('id_number') or '') and s['status'] == '招募搭档']
        fr2, tree2 = make_tree(lst2, ['申请ID', '姓名', '身份证号', '状态', '申请时间'],
                               {'申请ID': 60, '姓名': 100, '身份证号': 160, '状态': 90, '申请时间': 140},
                               height=5)
        fr2.pack(fill='both', expand=True)
        req_rows = {}
        for s in my_solo:
            for r in DB.solo_partner_requests(s['id'], status='待审核'):
                iid = tree2.insert('', 'end', values=(r['id'], r['name'], r['id_number'],
                                                      r['status'], (r['created_at'] or '')[:16]))
                req_rows[iid] = (r, s)
        if not my_solo:
            tk.Label(lst2, text='你当前不是「招募搭档」状态的单人候选人，暂无待审搭档申请。',
                     bg=COLOR_CONTENT_BG, font=('Arial', 10), fg=COLOR_TEXT_GRAY).pack(anchor='w')

        def act(approve):
            sel = tree2.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条搭档申请')
                return
            r, s = req_rows[sel[0]]
            if not messagebox.askyesno('确认', '%s 的搭档申请，确认%s？' % (
                    r['name'], '通过并正式参选' if approve else '拒绝')):
                return
            ok, msg = DB.review_solo_partner(r['id'], approve, self.user.get('id_number') or '')
            messagebox.showinfo('提示', msg)
            self._show_solo_admin()

        b2 = tk.Frame(lst2, bg=COLOR_CONTENT_BG)
        b2.pack(pady=5)
        make_flat_button(b2, '✅ 通过并正式参选', lambda: act(True), COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                         width=16, height=1).pack(side='left', padx=4)
        make_flat_button(b2, '❌ 拒绝', lambda: act(False), COLOR_DANGER, '#a93226',
                         width=10, height=1).pack(side='left', padx=4)

    def _show_message_admin(self):
        self._clear_main()
        make_page_title(self.main_frame, "📨 消息管理").pack(pady=20)

        form_f = tk.LabelFrame(self.main_frame, text="发送消息", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=15, pady=10)
        form_f.pack(fill=tk.X, padx=30, pady=5)
        row1 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row1.pack(fill='x', pady=4)
        tk.Label(row1, text="接收对象：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        recv_var = tk.StringVar(value='all')
        for text, val in [('全体用户', 'all'), ('全体民众', 'citizen'), ('全体官员', 'official'), ('指定个人', 'person')]:
            tk.Radiobutton(row1, text=text, variable=recv_var, value=val,
                           font=("Arial", 10), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        tk.Label(row1, text="身份证号(指定个人时)：", font=("Arial", 10), bg=COLOR_CONTENT_BG).pack(side='left', padx=(10, 5))
        person_e = make_groove_entry(row1, width=20)
        person_e.pack(side='left', padx=5)
        row2 = tk.Frame(form_f, bg=COLOR_CONTENT_BG)
        row2.pack(fill='x', pady=4)
        tk.Label(row2, text="标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        title_e = make_groove_entry(row2, width=42)
        title_e.pack(side='left', padx=5)
        tk.Label(form_f, text="内容：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w', padx=5)
        content_e = scrolledtext.ScrolledText(form_f, font=("Arial", 11), height=4, wrap='word')
        content_e.pack(fill='x', padx=5, pady=4)

        def send():
            title = title_e.get().strip()
            content = content_e.get('1.0', 'end').strip()
            if not title or not content:
                messagebox.showwarning('提示', '请填写标题和内容')
                return
            rv = recv_var.get()
            if rv == 'all':
                DB.send_message('all', '', title, content)
            elif rv == 'citizen':
                conn = DB.get_conn(GOV_DB)
                rows = conn.execute("SELECT id_number FROM residents").fetchall()
                conn.close()
                for rr in rows:
                    DB.send_message('citizen', rr['id_number'], title, content)
            elif rv == 'official':
                conn = DB.get_conn(GOV_DB)
                rows = conn.execute("SELECT id_number FROM officials WHERE status='在职'").fetchall()
                conn.close()
                for rr in rows:
                    DB.send_message('official', rr['id_number'], title, content)
            else:
                pid = person_e.get().strip()
                if not pid:
                    messagebox.showwarning('提示', '请填写指定个人的身份证号')
                    return
                DB.send_message('citizen', pid, title, content)
                DB.send_message('official', pid, title, content)
            DB.log(self.user.get('type') or 'admin', self.user.get('id', ''), self.user['name'],
                   '发送消息', f'发送消息《{title}》')
            messagebox.showinfo('成功', '消息发送成功')
            title_e.delete(0, 'end')
            content_e.delete('1.0', 'end')
            load()

        make_flat_button(form_f, '📨 发送消息', send, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14).pack(pady=6)

        list_f = tk.LabelFrame(self.main_frame, text="消息记录", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_f.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '标题', '接收对象', '内容', '状态', '时间')
        tree_frame, tree = make_tree(list_f, cols, {'id': 40, '标题': 180, '接收对象': 90, '内容': 340,
                                            '状态': 70, '时间': 140}, height=9)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 200").fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['title'], f"{r['receiver_type']}/{r['receiver_id'] or '全部'}",
                                                r['content'][:50] + ('...' if len(r['content']) > 50 else ''),
                                                '已读' if r['is_read'] else '未读', r['created_at']))

        load()

        def delete_msg():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请选择一条消息')
                return
            mid = tree.item(sel[0])['values'][0]
            if messagebox.askyesno('确认', '确定删除该消息吗？'):
                conn = DB.get_conn(GOV_DB)
                conn.execute("DELETE FROM messages WHERE id=?", (mid,))
                conn.commit()
                conn.close()
                load()

        btn_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_f.pack(pady=8)
        make_flat_button(btn_f, '🗑️ 删除消息', delete_msg, COLOR_DANGER, COLOR_DANGER_HOVER, width=12, height=1).pack(side='left', padx=5)

    # ========== 管理员：系统日志 ==========
    def _show_logs(self):
        self._clear_main()
        make_page_title(self.main_frame, "📝 系统日志").pack(pady=20)

        filter_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        filter_f.pack(pady=5)
        tk.Label(filter_f, text="操作类型：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left', padx=5)
        type_var = tk.StringVar(value='全部')
        ttk.Combobox(filter_f, textvariable=type_var, values=['全部', '登录', '退出', '添加官员', '删除官员', '升官', '降级',
                                                                '审批文件已通过', '审批文件已驳回', '处理举报', '起诉官员', '发布公告',
                                                                '在线申办', '办理办件', '发文', '服务评价', '回复咨询'],
                      font=("Arial", 11), width=18, state='readonly').pack(side='left', padx=5)

        list_frame = tk.LabelFrame(self.main_frame, text="操作日志", font=("Arial", 12, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=10, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)
        cols = ('id', '操作者', '类型', '操作', '详情', '时间')
        tree_frame, tree = make_tree(list_frame, cols, {'id': 40, '操作者': 90, '类型': 70, '操作': 120,
                                                '详情': 380, '时间': 150}, height=15)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        def load():
            for item in tree.get_children():
                tree.delete(item)
            conn = DB.get_conn(GOV_DB)
            t = type_var.get()
            if t == '全部':
                rows = conn.execute("SELECT * FROM system_logs ORDER BY id DESC LIMIT 500").fetchall()
            else:
                rows = conn.execute("SELECT * FROM system_logs WHERE action=? ORDER BY id DESC LIMIT 500", (t,)).fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['operator_name'], r['operator_type'], r['action'],
                                                r['details'], r['created_at']))

        load()
        type_var.trace_add('write', lambda *a: load())

    # ========== 管理员：数据统计 ==========
    def _show_stats(self):
        self._clear_main()
        make_page_title(self.main_frame, "📈 数据统计").pack(pady=20)

        conn = DB.get_conn(GOV_DB)

        # 官员统计卡片
        stats_frame = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        stats_frame.pack(fill=tk.X, padx=30, pady=10)
        stats = [
            ("官员总数", str(conn.execute("SELECT COUNT(*) FROM officials").fetchone()[0]), COLOR_PRIMARY, "👥"),
            ("在职官员", str(conn.execute("SELECT COUNT(*) FROM officials WHERE status='在职'").fetchone()[0]), COLOR_SUCCESS, "✅"),
            ("停职官员", str(conn.execute("SELECT COUNT(*) FROM officials WHERE status='停职'").fetchone()[0]), COLOR_WARNING, "⏸️"),
            ("已撤职", str(conn.execute("SELECT COUNT(*) FROM officials WHERE status='已撤职'").fetchone()[0]), COLOR_DANGER, "🚫"),
            ("已拉黑", str(conn.execute("SELECT COUNT(*) FROM officials WHERE status='已拉黑'").fetchone()[0]), '#c0392b', "⛔"),
        ]
        for i, (title, value, color, icon) in enumerate(stats):
            card = tk.Frame(stats_frame, relief=tk.RAISED, bd=2, bg=COLOR_CONTENT_BG, width=160, height=90)
            card.grid(row=0, column=i, padx=8, pady=10)
            card.grid_propagate(False)
            tk.Label(card, text=icon, font=("Arial", 20), bg=COLOR_CONTENT_BG).pack(pady=3)
            tk.Label(card, text=title, font=("Arial", 9), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack()
            tk.Label(card, text=value, font=("Arial", 13, "bold"), bg=COLOR_CONTENT_BG, fg=color).pack()

        # 办件统计卡片
        case_frame = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        case_frame.pack(fill=tk.X, padx=30, pady=5)
        cstats = [
            ("办件总数", str(conn.execute("SELECT COUNT(*) FROM service_cases").fetchone()[0]), COLOR_PRIMARY, "🗂️"),
            ("待受理", str(conn.execute("SELECT COUNT(*) FROM service_cases WHERE status='待受理'").fetchone()[0]), COLOR_WARNING, "📥"),
            ("办理中", str(conn.execute("SELECT COUNT(*) FROM service_cases WHERE status IN ('受理中','办理中')").fetchone()[0]), COLOR_PURPLE, "⏳"),
            ("已办结", str(conn.execute("SELECT COUNT(*) FROM service_cases WHERE status='已办结'").fetchone()[0]), COLOR_SUCCESS, "✅"),
            ("已退回", str(conn.execute("SELECT COUNT(*) FROM service_cases WHERE status='已退回'").fetchone()[0]), COLOR_DANGER, "↩️"),
        ]
        for i, (title, value, color, icon) in enumerate(cstats):
            card = tk.Frame(case_frame, relief=tk.RAISED, bd=2, bg=COLOR_CONTENT_BG, width=160, height=90)
            card.grid(row=0, column=i, padx=8, pady=10)
            card.grid_propagate(False)
            tk.Label(card, text=icon, font=("Arial", 20), bg=COLOR_CONTENT_BG).pack(pady=3)
            tk.Label(card, text=title, font=("Arial", 9), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack()
            tk.Label(card, text=value, font=("Arial", 13, "bold"), bg=COLOR_CONTENT_BG, fg=color).pack()

        # 部门分布
        dept_f = tk.LabelFrame(self.main_frame, text="各部门官员分布", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        dept_f.pack(fill=tk.X, padx=30, pady=10)
        dept_rows = conn.execute("SELECT department, COUNT(*) as cnt FROM officials WHERE status='在职' GROUP BY department ORDER BY cnt DESC").fetchall()
        tree = ttk.Treeview(dept_f, columns=('部门', '官员数'), show="headings", height=6)
        tree.heading('部门', text='部门')
        tree.heading('官员数', text='官员数')
        tree.column('部门', width=200, anchor=tk.CENTER)
        tree.column('官员数', width=100, anchor=tk.CENTER)
        tree.pack(fill=tk.X)
        for r in dept_rows:
            tree.insert('', 'end', values=(r['department'], r['cnt']))

        # 办件状态分布
        st_f = tk.LabelFrame(self.main_frame, text="办件状态分布", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=10, pady=10)
        st_f.pack(fill=tk.X, padx=30, pady=10)
        st_rows = conn.execute("SELECT status, COUNT(*) as cnt FROM service_cases GROUP BY status ORDER BY cnt DESC").fetchall()
        tree2 = ttk.Treeview(st_f, columns=('状态', '数量'), show="headings", height=6)
        tree2.heading('状态', text='状态')
        tree2.heading('数量', text='数量')
        tree2.column('状态', width=200, anchor=tk.CENTER)
        tree2.column('数量', width=100, anchor=tk.CENTER)
        tree2.pack(fill=tk.X)
        for r in st_rows:
            tree2.insert('', 'end', values=(r['status'], r['cnt']))

        # 审批文件统计
        file_f = tk.LabelFrame(self.main_frame, text="审批文件统计", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=10)
        file_f.pack(fill=tk.X, padx=30, pady=10)
        fstats = [
            ("总申请数", str(conn.execute("SELECT COUNT(*) FROM approval_files").fetchone()[0])),
            ("待审批", str(conn.execute("SELECT COUNT(*) FROM approval_files WHERE status='待审批'").fetchone()[0])),
            ("已通过", str(conn.execute("SELECT COUNT(*) FROM approval_files WHERE status='已通过'").fetchone()[0])),
            ("已驳回", str(conn.execute("SELECT COUNT(*) FROM approval_files WHERE status='已驳回'").fetchone()[0])),
            ("举报总数", str(conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0])),
            ("咨询总数", str(conn.execute("SELECT COUNT(*) FROM consultations").fetchone()[0])),
        ]
        for i, (label, val) in enumerate(fstats):
            tk.Label(file_f, text=f'{label}：{val}', font=("Arial", 12), bg=COLOR_CONTENT_BG).grid(
                row=i // 3, column=(i % 3) * 2, padx=20, pady=10)
        conn.close()

    # ========== 官员名录（民众端 + 官员端 · 信息公开） ==========
    def _portrait_viewer(self, off):
        """大图查看官员肖像（白底居中，姓名 + 职务）"""
        win = tk.Toplevel(self.main_frame)
        win.title(f"肖像 · {off['name']}")
        win.geometry('460x620')
        win.configure(bg='white')
        win.transient(self.main_frame)
        try:
            _ppath = off['photo'] or ''
        except Exception:
            _ppath = ''
        ph, has = make_portrait_photo(_ppath, 340, True, 'white')
        if ph:
            lbl = tk.Label(win, image=ph, bg='white', bd=0)
            lbl.image = ph
            lbl.pack(pady=(40, 18))
        else:
            tk.Label(win, text='👤', font=("Arial", 100), bg='white', fg='#c7c7cc').pack(pady=(60, 20))
        tk.Label(win, text=off['name'], font=("Arial", 24, "bold"),
                 bg='white', fg=COLOR_TITLE).pack()
        _sub = off['position'] or ''
        if off['department']:
            _sub += f" · {off['department']}"
        tk.Label(win, text=_sub, font=("Arial", 12), bg='white', fg='#6e6e73').pack(pady=(4, 0))
        _pty, _intro = '', ''
        try:
            _pty = off['party'] or ''
            _intro = off['intro'] or ''
        except Exception:
            pass
        if _pty:
            tk.Label(win, text=f"所属党派：{_pty}", font=("Arial", 10),
                     bg='white', fg='#86868b').pack(pady=(6, 0))
        if _intro:
            tk.Label(win, text=_intro[:150], font=("Arial", 10), bg='white',
                     fg='#6e6e73', wraplength=390, justify='center').pack(pady=(10, 0), padx=20)
        if not has:
            tk.Label(win, text='该官员暂未上传肖像', font=("Arial", 10),
                     bg='white', fg='#9a9a9f').pack(pady=(10, 0))
        make_flat_button(win, '关闭', win.destroy, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=10, height=1).pack(pady=16)

    def _show_official_directory(self):
        self._clear_main()
        make_page_title(self.main_frame, "👥 官员名录 · 信息公开").pack(pady=12)
        tk.Label(self.main_frame,
                 text="总统 / 副总统任期默认 4 年 · 换届自动停止 · 连任在就职日基础上再 +4 年\n点击表格行查看官员肖像、完整信息与任期详情",
                 font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY, justify='center').pack(pady=(0, 8))

        bar = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        bar.pack(pady=(0, 8))
        kw_var = tk.StringVar()
        kw_entry = make_groove_entry(bar, width=24)
        kw_entry.config(textvariable=kw_var)
        kw_entry.pack(side='left', padx=6)
        make_flat_button(bar, '🔍 搜索', lambda: load(kw_var.get().strip()),
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=10, height=1).pack(side='left', padx=4)
        make_flat_button(bar, '🔄 刷新', lambda: (kw_var.set(''), load('')),
                         COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=10, height=1).pack(side='left', padx=4)

        frame, tree = make_tree(self.main_frame,
                                ['肖像', '姓名', '党派', '职务', '部门', '职级', '状态', '任期', '卸任日'],
                                {'肖像': 88, '姓名': 100, '党派': 100, '职务': 140, '部门': 130,
                                 '职级': 130, '状态': 80, '任期': 190, '卸任日': 105},
                                height=17)
        frame.pack(fill='both', expand=True, padx=25)
        tree.tag_configure('off', foreground='#a0a0a0')

        stat_lbl = tk.Label(self.main_frame, text='', font=("Arial", 10),
                            bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY)
        stat_lbl.pack(pady=6)

        rows = {}

        def load(kw=''):
            tree.delete(*tree.get_children())
            rows.clear()
            data = DB.official_directory()
            i = 0
            for off, ten in data:
                name = off['name']
                if kw and kw not in name and kw not in (off['position'] or '') \
                        and kw not in (off['department'] or '') and kw not in (off['rank'] or ''):
                    continue
                iid = str(i)
                try:
                    _pp = off['photo'] or ''
                except Exception:
                    _pp = ''
                try:
                    _party = off['party'] or '—'
                except Exception:
                    _party = '—'
                tree.insert('', 'end', iid=iid,
                            values=('🖼 已上传' if _pp else '—',
                                    name, _party, off['position'], off['department'], off['rank'],
                                    off['status'], ten['term_text'], ten['resign_date']),
                            tags=('off',) if off['status'] != '在职' else ())
                rows[iid] = (off, ten)
                i += 1
            stat_lbl.config(text=f"共 {len(data)} 名官员 · 当前显示 {i} 人")

        def show_detail(_evt=None):
            sel = tree.selection()
            if not sel:
                return
            off, ten = rows[sel[0]]
            self._official_detail_dialog(off, ten)

        tree.bind('<<TreeviewSelect>>', show_detail)
        load('')

    def _official_detail_dialog(self, off, ten):
        win = tk.Toplevel(self.main_frame)
        win.title(f"官员详情 · {off['name']}")
        win.geometry('640x900')
        win.configure(bg='white')
        win.resizable(False, False)
        win.transient(self.main_frame)
        win.grab_set()

        # 头部：左肖像 + 右侧职务/姓名/状态
        head = tk.Frame(win, bg='white')
        head.pack(fill='x', padx=24, pady=(18, 6))
        try:
            _ppath = off['photo'] or ''
        except Exception:
            _ppath = ''
        _ph, _has_photo = make_portrait_photo(_ppath, 100, True, 'white')
        _left = tk.Frame(head, bg='white')
        _left.pack(side='left', padx=(0, 16))
        if _ph:
            _plbl = tk.Label(_left, image=_ph, bg='white', cursor='hand2')
            _plbl.image = _ph
            _plbl.pack()
        else:
            tk.Label(_left, text='👤', font=("Arial", 44), bg='white',
                     fg='#b8bec6').pack()
        _txt = tk.Frame(head, bg='white')
        _txt.pack(side='left', fill='y', pady=6)
        tk.Label(_txt, text=off['position'], font=("Arial", 13, "bold"),
                 bg='white', fg=COLOR_PRIMARY).pack(anchor='w')
        tk.Label(_txt, text=off['name'], font=("Arial", 20, "bold"),
                 bg='white', fg=COLOR_TITLE).pack(anchor='w')
        st = off['status']
        st_color = '#34c759' if st == '在职' else COLOR_WARNING
        tk.Label(_txt, text=f"[{st}]", font=("Arial", 12, "bold"),
                 bg='white', fg=st_color).pack(anchor='w')
        _view_btn = tk.Label(_txt, text='🔍 查看大图', font=("Arial", 9, "underline"),
                             bg='white', fg=COLOR_PRIMARY, cursor='hand2')
        _view_btn.pack(anchor='w', pady=(4, 0))
        _view_btn.bind('<Button-1>', lambda _e: self._portrait_viewer(off))

        # 基本信息（两列网格，紧凑布局）
        info = tk.LabelFrame(win, text="📇 基本信息", font=("Arial", 11, "bold"),
                             bg='white', padx=14, pady=6)
        info.pack(fill='x', padx=24, pady=6)
        info.grid_columnconfigure(1, weight=1)
        info.grid_columnconfigure(3, weight=1)
        rows_def = [
            ('身份证号', DB.mask_id(off['id_number']), None, None),
            ('性别', off['gender'] or '—', '部门', off['department'] or '—'),
            ('职级', off['rank'] or '—', '任命日期', (off['appointed_date'] or '—')[:19]),
            ('联系电话', off['phone'] or '—', '住址', off['address'] or '—'),
        ]
        try:
            rows_def.append(('所属党派', off['party'] or '—', None, None))
        except Exception:
            rows_def.append(('所属党派', '—', None, None))
        for _r, (k1, v1, k2, v2) in enumerate(rows_def):
            tk.Label(info, text=k1, font=("Arial", 10, "bold"), bg='white',
                     fg=COLOR_TEXT_GRAY, width=8, anchor='w').grid(
                row=_r, column=0, sticky='w', pady=2, padx=(0, 6))
            l1 = tk.Label(info, text=v1, font=("Arial", 10), bg='white',
                          fg='#333', anchor='w')
            if k2 is None:
                l1.grid(row=_r, column=1, columnspan=3, sticky='we', pady=2)
            else:
                l1.grid(row=_r, column=1, sticky='w', pady=2)
                tk.Label(info, text=k2, font=("Arial", 10, "bold"), bg='white',
                         fg=COLOR_TEXT_GRAY, width=8, anchor='w').grid(
                    row=_r, column=2, sticky='w', pady=2, padx=(16, 6))
                tk.Label(info, text=v2, font=("Arial", 10), bg='white',
                         fg='#333', anchor='w').grid(row=_r, column=3, sticky='w', pady=2)

        # 官员介绍（所有用户可见）
        try:
            _intro = off['intro'] or ''
        except Exception:
            _intro = ''
        intro_box = tk.LabelFrame(win, text="📝 官员介绍", font=("Arial", 11, "bold"),
                                  bg='white', padx=14, pady=8)
        intro_box.pack(fill='x', padx=24, pady=6)
        tk.Label(intro_box, text=_intro or '该官员暂未填写介绍',
                 font=("Arial", 10), bg='#f4f6fa', fg='#444', wraplength=545,
                 justify='left', anchor='w', padx=8, pady=8).pack(fill='x')

        # 任期详情
        ten_f = tk.LabelFrame(win, text="⏳ 任期与卸任", font=("Arial", 11, "bold"),
                              bg='white', padx=14, pady=8)
        ten_f.pack(fill='x', padx=24, pady=6)
        ten_rows = [
            ('任期类型', ten['kind']),
            ('任期起止', ten['term_text']),
            ('卸任日', ten['resign_date']),
            ('当选届数', f"{ten['terms']} 届" if ten['terms'] else '—'),
        ]
        for k, v in ten_rows:
            row = tk.Frame(ten_f, bg='white')
            row.pack(fill='x', pady=2)
            tk.Label(row, text=k, font=("Arial", 10, "bold"), bg='white',
                     fg=COLOR_TEXT_GRAY, width=8, anchor='w').pack(side='left')
            tk.Label(row, text=v, font=("Arial", 10, "bold"), bg='white',
                     fg=COLOR_TITLE if k in ('卸任日',) else '#333').pack(side='left')
        tk.Label(ten_f, text="📌 " + ten['note'], font=("Arial", 9),
                 bg='#f4f6fa', fg='#666', wraplength=500, justify='left',
                 padx=8, pady=6).pack(fill='x', pady=(6, 2))

        # 官员档案（历任职务与任期 + 大选当选/落选记录）
        career_f = tk.LabelFrame(win, text="📂 官员档案（历任职务 · 每次任期 · 竞选记录）",
                                 font=("Arial", 11, "bold"), bg='white', padx=8, pady=6)
        career_f.pack(fill='both', expand=True, padx=24, pady=6)
        careers = DB.official_careers(off['id'])
        if not careers:
            tk.Label(career_f, text='暂无档案记录（任命制官员履历随任命/调整自动累计）',
                     font=("Arial", 9), bg='white', fg=COLOR_TEXT_GRAY, pady=10).pack()
        else:
            car_tree_f, car_tree = make_tree(
                career_f, ('类型', '职务', '任期', '说明'),
                {'类型': 56, '职务': 70, '任期': 172, '说明': 240}, height=6, anchor='w')
            car_tree_f.pack(fill='both', expand=True)
            for cv in careers:
                car_tree.insert('', 'end', values=(
                    cv['record_type'], cv['position'], cv['term_text'], cv['note']))
            for _col in ('类型', '职务', '任期', '说明'):
                car_tree.heading(_col, text=_col)

            def show_career_detail(_ev=None):
                sel = car_tree.selection()
                if not sel:
                    return
                idx = car_tree.index(sel[0])
                cv = careers[idx]
                dlg = tk.Toplevel(win)
                dlg.title(f'履历详情 · {off["name"]}')
                dlg.geometry('480x360')
                dlg.configure(bg='white')
                dlg.resizable(False, False)
                dlg.transient(win)
                dlg.grab_set()
                tk.Label(dlg, text=f"📂 履历详情 · {cv['record_type']}",
                         font=("Arial", 13, "bold"), bg='white', fg=COLOR_PRIMARY).pack(pady=(16, 8))
                frm = tk.Frame(dlg, bg='white')
                frm.pack(fill='both', expand=True, padx=28)
                _kv = [
                    ('记录类型', cv['record_type']),
                    ('职　　务', cv['position']),
                    ('部　　门', cv['department'] or '—'),
                    ('任期起止', cv['term_text']),
                    ('记录时间', cv['created_at'] or '—'),
                    ('关联大选', f"第 {cv['election_id']} 届大选" if cv['election_id'] else '非竞选记录'),
                ]
                for i, (k, v) in enumerate(_kv):
                    tk.Label(frm, text=k, font=("Arial", 10, "bold"), bg='white',
                             fg=COLOR_TEXT_GRAY, anchor='e').grid(
                        row=i, column=0, sticky='e', pady=5, padx=(0, 12))
                    tk.Label(frm, text=str(v), font=("Arial", 10), bg='white',
                             fg='#333', anchor='w', wraplength=280, justify='left').grid(
                        row=i, column=1, sticky='w', pady=5)
                if cv['note']:
                    tk.Label(frm, text='说　明：', font=("Arial", 10, "bold"), bg='white',
                             fg=COLOR_TEXT_GRAY, anchor='e').grid(
                        row=len(_kv), column=0, sticky='ne', pady=5, padx=(0, 12))
                    tk.Label(frm, text=cv['note'], font=("Arial", 10), bg='#f4f6fa',
                             fg='#444', anchor='w', justify='left', wraplength=300,
                             padx=8, pady=6).grid(row=len(_kv), column=1, sticky='we', pady=5)
                make_flat_button(dlg, '关 闭', dlg.destroy, COLOR_PRIMARY,
                                 COLOR_PRIMARY_HOVER, width=12, height=1).pack(pady=12)

            car_tree.bind('<Double-1>', show_career_detail)
            tk.Label(career_f, text='💡 双击档案记录可查看详情', font=("Arial", 9),
                     bg='white', fg=COLOR_TEXT_GRAY).pack(pady=(4, 0))

        make_flat_button(win, '关 闭', win.destroy, COLOR_PRIMARY,
                         COLOR_PRIMARY_HOVER, width=12, height=1).pack(pady=12)

    # ========== 议会中心（议员/法案/发起大选） ==========
    # ========== 政治结构总览（实时） ==========
    def _show_political_structure(self):
        self._clear_main()
        make_page_title(self.main_frame, "🏛️ 政治结构总览（实时数据 · 可随时刷新）").pack(pady=15)

        bar = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        bar.pack(pady=(0, 10))
        make_flat_button(bar, '🔄 刷新结构', self._show_political_structure,
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14, height=1).pack()

        conn = DB.get_conn(GOV_DB)
        def q(sql, args=()):
            return conn.execute(sql, args).fetchall()

        pres = q("SELECT * FROM officials WHERE position='总统' AND status='在职'")
        vp = q("SELECT * FROM officials WHERE position='副总统' AND status='在职'")
        sec = q("SELECT * FROM officials WHERE position='国务卿' AND status='在职'")
        ministers = q("""SELECT * FROM officials WHERE status='在职'
                         AND (position LIKE '%部长%' OR position LIKE '%署长%' OR position LIKE '%局长%')
                         AND position NOT LIKE '副%' AND position != '国务卿' ORDER BY department""")
        senators = q("SELECT * FROM officials WHERE status='在职' AND position IN ('参议员','参议院议长') ORDER BY id")
        reps = q("SELECT * FROM officials WHERE status='在职' AND position IN ('众议员','众议院议长') ORDER BY id")
        justices = q("SELECT * FROM officials WHERE status='在职' AND position LIKE '%大法官%' ORDER BY id")
        governors = q("SELECT * FROM officials WHERE status='在职' AND position='州长' ORDER BY department")
        parties = q("SELECT * FROM parties ORDER BY id")
        elects = q("SELECT * FROM elections ORDER BY id")
        total_off = q("SELECT COUNT(*) n FROM officials WHERE status='在职'")[0]['n']
        # 现任总统是否临时（无任何当选记录）
        acting_pres = False
        if pres:
            pres_term_rec = q("SELECT COUNT(*) n FROM elections WHERE status='已结束' "
                              "AND (winner_id=? OR winner_name=?)", (pres[0]['id'], pres[0]['name']))[0]['n']
            acting_pres = pres_term_rec == 0
        conn.close()
        const = DB.current_constitution()

        def person_line(parent, title, rows, missing_txt='空缺'):
            """rows: list of dict with name/position/department/rank"""
            if not rows:
                tk.Label(parent, text=f"{title}：{missing_txt}", font=("Arial", 10),
                         bg=parent.cget('bg'), fg=COLOR_WARNING).pack(anchor='w')
                return
            for r in rows:
                tk.Label(parent, text=f"{title}：{r['name']}（{r['department']} · {r['rank']}）",
                         font=("Arial", 10), bg=parent.cget('bg'), fg='#444').pack(anchor='w')

        # ---- 宪制信息条 ----
        info = tk.LabelFrame(self.main_frame, text="📜 现行宪制", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=15, pady=8)
        info.pack(fill='x', padx=25)
        if const:
            c_items = [x.strip() for x in (const.get('items') or '').splitlines() if x.strip()]
            tk.Label(info, text=f"《{const.get('title')}》第 {const.get('version')} 版 · 共 {len(c_items)} 条 · 生效于 {const.get('passed_at', '')[:19]}",
                     font=("Arial", 11, "bold"), bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).pack(anchor='w')
        else:
            tk.Label(info, text="尚未修宪", font=("Arial", 11, "bold"),
                     bg=COLOR_CONTENT_BG, fg=COLOR_WARNING).pack(anchor='w')
        e_txt = " · ".join([f"第{e['id']}次大选：{e['winner_name']} 当选（{e['term_start'][:16]} 起任）" for e in elects]) or "暂无大选记录"
        tk.Label(info, text=f"🗳️ {e_txt}　|　在职官员 {total_off} 人",
                 font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#555').pack(anchor='w', pady=(4, 0))

        # ---- 三权分立 ----
        row = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        row.pack(fill='x', padx=25, pady=8)

        adm = tk.LabelFrame(row, text="🏛️ 行政分支（总统统领）", font=("Arial", 11, "bold"),
                            bg=COLOR_CONTENT_BG, padx=12, pady=8)
        adm.pack(side='left', fill='both', expand=True, padx=(0, 8))
        person_line(adm, '👑 总统' + ('（临时总统）' if acting_pres else ''), pres)
        person_line(adm, '🛡 副总统', vp)
        person_line(adm, '🏛 国务卿', sec)
        tk.Label(adm, text="内阁各部负责人：", font=("Arial", 10, "bold"),
                 bg=COLOR_CONTENT_BG).pack(anchor='w', pady=(4, 0))
        if ministers:
            for r in ministers:
                tk.Label(adm, text=f"  · {r['position']}：{r['name']}",
                         font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#444').pack(anchor='w')
        else:
            tk.Label(adm, text="  · 暂无", font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_WARNING).pack(anchor='w')

        leg = tk.LabelFrame(row, text="⚖️ 立法分支（议会）", font=("Arial", 11, "bold"),
                            bg=COLOR_CONTENT_BG, padx=12, pady=8)
        leg.pack(side='left', fill='both', expand=True, padx=4)
        person_line(leg, '参议院', senators)
        person_line(leg, '众议院', reps)
        tk.Label(leg, text="📜 修宪权：三分之二议员表决通过即生效",
                 font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#555').pack(anchor='w', pady=(4, 0))

        jud = tk.LabelFrame(row, text="⚖️ 司法分支", font=("Arial", 11, "bold"),
                            bg=COLOR_CONTENT_BG, padx=12, pady=8)
        jud.pack(side='left', fill='both', expand=True, padx=(8, 0))
        person_line(jud, '首席大法官', [r for r in justices if '首席' in r['position']])
        person_line(jud, '大法官', [r for r in justices if '首席' not in r['position']])
        tk.Label(jud, text="上诉法院 / 地区法院：暂无在职",
                 font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_WARNING).pack(anchor='w', pady=(4, 0))

        # ---- 党派 + 地方 ----
        row2 = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        row2.pack(fill='x', padx=25, pady=4)

        pty = tk.LabelFrame(row2, text="🎌 党派", font=("Arial", 11, "bold"),
                            bg=COLOR_CONTENT_BG, padx=12, pady=8)
        pty.pack(side='left', fill='both', expand=True, padx=(0, 8))
        if parties:
            for pt in parties:
                lr = q_leader = None
                lconn = DB.get_conn(GOV_DB)
                lr = lconn.execute("SELECT name FROM officials WHERE id=? AND status='在职'",
                                   (pt['leader_id'],)).fetchone()
                lconn.close()
                tk.Label(pty, text=f"  · {pt['name']}：党魁 {lr['name'] if lr else '(未在职)'}",
                         font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#444').pack(anchor='w')
        else:
            tk.Label(pty, text="暂无党派", font=("Arial", 10), bg=COLOR_CONTENT_BG,
                     fg=COLOR_WARNING).pack(anchor='w')

        st = tk.LabelFrame(row2, text="🗺️ 六州", font=("Arial", 11, "bold"),
                           bg=COLOR_CONTENT_BG, padx=12, pady=8)
        st.pack(side='left', fill='both', expand=True, padx=(8, 0))
        person_line(st, '州长（全州）', governors)

    def _show_parliament(self):
        self._clear_main()
        make_page_title(self.main_frame, "🏛️ 议会中心（三权分立 · 立法分支）").pack(pady=15)

        # ---- 现行宪制信息卡（修宪生效前后切换文案） ----
        conn = DB.get_conn(GOV_DB)
        senators = conn.execute("SELECT COUNT(*) n FROM officials WHERE position IN ('参议员','参议院议长') AND status='在职'").fetchone()['n']
        reps = conn.execute("SELECT COUNT(*) n FROM officials WHERE position IN ('众议员','众议院议长') AND status='在职'").fetchone()['n']
        elections = conn.execute("SELECT COUNT(*) n FROM elections").fetchone()['n']
        pres = conn.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
        # 现任总统是否临时（无任何当选记录）
        acting_pres = False
        if pres:
            pres_term_rec = conn.execute("SELECT COUNT(*) n FROM elections WHERE status='已结束' "
                                         "AND (winner_id=? OR winner_name=?)",
                                         (pres['id'], pres['name'])).fetchone()['n']
            acting_pres = pres_term_rec == 0
        conn.close()
        const = DB.current_constitution()

        info_f = tk.LabelFrame(self.main_frame, text="🏛️ 现行宪制", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=15, pady=10)
        info_f.pack(fill='x', padx=25)
        tk.Label(info_f, text=f"参议员 {senators} 人　|　众议员 {reps} 人　|　已举办大选 {elections} 次",
                 font=("Arial", 12), bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).pack(pady=4)
        # 现任总统板块（无任何当选记录 → 临时总统）
        if acting_pres:
            pres_txt = f"👑 现任总统：{pres['name']}（临时总统）"
        elif pres:
            pres_txt = f"👑 现任总统：{pres['name']}（{pres['rank']}）"
        else:
            pres_txt = "👑 现任总统：空缺"
        tk.Label(info_f, text=pres_txt, font=("Arial", 12, "bold"),
                 bg=COLOR_CONTENT_BG, fg=COLOR_PURPLE).pack(anchor='w', pady=(4, 0))
        if acting_pres:
            tk.Label(info_f, text="⏳ 现任总统为临时总统：总统任期（届数）自 2026-10-23 第一轮大选起正式记录；"
                                  "当选者于次年 1 月 20 日就职后任期四年，临时执政期间不计入两届限制。",
                     font=("Arial", 10, "bold"), bg=COLOR_CONTENT_BG, fg=COLOR_WARNING,
                     justify='left', wraplength=1050).pack(anchor='w', pady=(2, 0))
        if const:
            tk.Label(info_f, text="由公民直选产生 · 总统任期四年，固定周期大选，议会无权随时发起大选；"
                                  "仅弹劾、总统丧失履职能力、国家重大危机时可启动补选 · 任至本届任期结束",
                     font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#555', justify='left',
                     wraplength=1050).pack(anchor='w')
            tk.Label(info_f, text="📜 立法权（权力点 9 项）· 审议法案、发起弹劾，无权随时发起总统大选；"
                                  "仅弹劾、总统丧失履职能力、国家重大危机情形下，可启动总统补选",
                     font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#555', justify='left',
                     wraplength=1050).pack(anchor='w')
            # 宪法修正案卡片（内容过长默认折叠，可展开查看全文）
            const_items = (const.get('items') or '').strip().splitlines()
            tk.Label(info_f, text=f"📜 {const.get('title') or '蛋国宪法'}（第 {const.get('version')} 版 · 生效于 {const.get('passed_at', '')[:10]}）",
                     font=("Arial", 11, "bold"), bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).pack(anchor='w', pady=(6, 2))
            make_foldable_content(info_f, const_items)
        else:
            tk.Label(info_f, text="由公民直选产生 · 议会可随时发起大选 · 任至本次大选结果产生",
                     font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#555', justify='left',
                     wraplength=1050).pack(anchor='w')
            tk.Label(info_f, text="📜 立法权（权力点 9 项）· 有权随时发起总统大选（现行旧宪制，修宪后调整）",
                     font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#555', justify='left',
                     wraplength=1050).pack(anchor='w')

        # 议会议员名单
        mem_f = tk.LabelFrame(self.main_frame, text="议会议员名单", font=("Arial", 12, "bold"),
                              bg=COLOR_CONTENT_BG, padx=10, pady=8)
        mem_f.pack(fill='both', expand=True, padx=25, pady=10)
        cols = ('id', '姓名', '职务', '所属议院', '级别', '状态')
        tf, tree = make_tree(mem_f, cols, {'id': 50, '姓名': 100, '职务': 150,
                                           '所属议院': 150, '级别': 140, '状态': 70}, height=8)
        tf.pack(fill='both', expand=True)

        def load_members():
            for it in tree.get_children():
                tree.delete(it)
            conn = DB.get_conn(GOV_DB)
            rows = conn.execute('''SELECT * FROM officials WHERE position IN
                ('参议员','参议院议长','众议员','众议院议长') AND status='在职'
                ORDER BY CASE WHEN position='副部长' THEN 32
                ELSE COALESCE((SELECT sort_order FROM positions WHERE name=officials.position),999) END, id''').fetchall()
            conn.close()
            for r in rows:
                tree.insert('', 'end', values=(r['id'], r['name'], r['position'], r['department'],
                                               r['rank'], r['status']))
        load_members()

        btns = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btns.pack(pady=8)
        # 发起大选：管理员或拥有 initiate_election 权力的官员
        if self.user['type'] == 'admin' or (self.user['type'] == 'official' and DB.can(self.user, 'initiate_election')):
            make_flat_button(btns, "🗳️ 发起总统大选", self._initiate_election_dialog,
                             COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=16).pack(side='left', padx=8)
        make_flat_button(btns, "📜 修宪审议", self._show_constitution,
                         COLOR_PURPLE, COLOR_PURPLE, width=14).pack(side='left', padx=8)
        make_flat_button(btns, "🗳️ 大选管理", self._show_election_center,
                         COLOR_PURPLE, COLOR_PURPLE, width=14).pack(side='left', padx=8)
        make_flat_button(btns, "🔄 刷新", lambda: (self._show_parliament()),
                         COLOR_GRAY, COLOR_GRAY, width=10).pack(side='left', padx=8)

    def _initiate_election_dialog(self):
        win = tk.Toplevel(self.root)
        win.title('发起总统大选')
        win.geometry('460x320')
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text="发起总统大选（议会发起，进入候选人登记阶段）",
                 font=("Arial", 12, "bold")).pack(pady=10)
        tk.Label(win, text="大选标题：").pack()
        title_e = make_groove_entry(win, width=30)
        title_e.insert(0, '总统大选')
        title_e.pack(pady=6)
        tk.Label(win, text="大选类型：").pack()
        type_var = tk.StringVar(value='regular')
        type_cb = ttk.Combobox(win, textvariable=type_var, state='readonly', width=27,
                               values=['定期大选（任期四年届满）', '补选（弹劾/丧失能力/重大危机）'])
        type_cb.pack(pady=6)
        tk.Label(win, text="补选原因（仅补选需选择）：").pack()
        reason_var = tk.StringVar(value='弹劾定罪')
        reason_cb = ttk.Combobox(win, textvariable=reason_var, state='readonly', width=27,
                                 values=['弹劾定罪', '总统丧失履职能力', '国家重大危机'])
        reason_cb.pack(pady=6)

        def do():
            title = title_e.get().strip() or '总统大选'
            e_type = 'regular' if type_var.get().startswith('定期') else 'by'
            reason = '' if e_type == 'regular' else reason_var.get()
            who = self.user['id'] if self.user['type'] in ('official', 'admin') else 0
            who_name = self.user['name']
            ok, msg, eid = DB.initiate_election(title, str(who), who_name, e_type, reason)
            if ok:
                DB.log(self.user['type'], self.user.get('id', self.user.get('id_number', '')),
                       self.user['name'], '发起大选', f'议会发起《{title}》类型={e_type} 原因={reason}')
                messagebox.showinfo('成功', msg)
                win.destroy()
                self._show_parliament()
            else:
                messagebox.showerror('失败', msg)
        make_flat_button(win, '确认发起', do, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=14).pack(pady=12)

    # ========== 修宪审议（总统提交 · 议会审议 · 通过即生效） ==========
    def _show_constitution(self):
        self._clear_main()
        make_page_title(self.main_frame, "📜 修宪审议（总统提交草案 · 议会投票 · 通过即自动生效）").pack(pady=15)
        const = DB.current_constitution()
        is_pres = self.user['type'] == 'official' and self.user.get('position') == '总统'
        is_leg = DB.is_legislator(self.user)
        is_speaker = self.user['type'] == 'official' and self.user.get('position') in ('参议院议长', '众议院议长')
        is_admin = self.user['type'] == 'admin'

        # ---- 现行宪法 ----
        top = tk.LabelFrame(self.main_frame, text="现行宪法", font=("Arial", 12, "bold"),
                            bg=COLOR_CONTENT_BG, padx=15, pady=8)
        top.pack(fill='x', padx=25)
        if const:
            items = (const.get('items') or '').strip().splitlines()
            tk.Label(top, text=f"《{const.get('title')}》第 {const.get('version')} 版 · 生效于 {const.get('passed_at', '')[:19]}",
                     font=("Arial", 11, "bold"), bg=COLOR_CONTENT_BG, fg=COLOR_TITLE).pack(anchor='w')
            make_foldable_content(top, items)
        else:
            tk.Label(top, text="尚未修宪（现行旧宪制：议会可随时发起总统大选，无任期限制）。",
                     font=("Arial", 11), bg=COLOR_CONTENT_BG, fg=COLOR_WARNING).pack(anchor='w')

        # ---- 总统提交草案 ----
        if is_pres:
            sub = tk.LabelFrame(self.main_frame, text="👑 总统提交修宪草案", font=("Arial", 12, "bold"),
                                bg=COLOR_CONTENT_BG, padx=15, pady=8)
            sub.pack(fill='x', padx=25, pady=10)
            tk.Label(sub, text="修正案标题：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w')
            t_e = make_groove_entry(sub, width=40)
            t_e.insert(0, '蛋国宪法修正案')
            t_e.pack(anchor='w', pady=(2, 6))
            tk.Label(sub, text="修正条文（每条一行，提交后由议会审议）：", font=("Arial", 11),
                     bg=COLOR_CONTENT_BG).pack(anchor='w')
            txt = tk.Text(sub, width=100, height=6, font=("Arial", 10))
            txt.pack(anchor='w', pady=(2, 6))
            txt.insert('1.0', "1. 总统任期四年，定期举行总统大选。\n"
                              "2. 同一公民当选总统累计不得超过两届，无论两届是否连续。\n"
                              "3. 议会不再拥有随时发起总统大选的权力。只有弹劾定罪、总统丧失履职能力、国家重大危机，才可以启动总统补选。")
            def do_submit():
                title = t_e.get().strip()
                content = txt.get('1.0', 'end').strip()
                if not title or not content:
                    messagebox.showwarning('提示', '请填写标题与条文'); return
                ok, msg, aid = DB.submit_amendment(title, content, self.user['id'], self.user['name'])
                if ok:
                    DB.log('official', self.user['id'], self.user['name'], '提交修宪草案', f'《{title}》')
                    messagebox.showinfo('成功', msg)
                    self._show_constitution()
                else:
                    messagebox.showerror('失败', msg)
            make_flat_button(sub, '提交草案送议会审议', do_submit, COLOR_SUCCESS, COLOR_SUCCESS_HOVER,
                             width=18).pack(anchor='w', pady=6)

        # ---- 审议区 ----
        rev = tk.LabelFrame(self.main_frame, text="修宪草案审议", font=("Arial", 12, "bold"),
                            bg=COLOR_CONTENT_BG, padx=10, pady=8)
        rev.pack(fill='both', expand=True, padx=25, pady=10)
        cols = ('id', '标题', '发起人', '状态', '赞成', '反对', '弃权', '提交时间')
        tf, tree = make_tree(rev, cols, {'id': 40, '标题': 220, '发起人': 90, '状态': 80,
                                         '赞成': 60, '反对': 60, '弃权': 60, '提交时间': 150}, height=6)
        tf.pack(fill='both', expand=True)

        def load_rev():
            for it in tree.get_children():
                tree.delete(it)
            for a in DB.list_amendments():
                st = a['status']
                tree.insert('', 'end', values=(a['id'], a['title'], a['proposer_name'], st,
                                               a['yes_count'], a['no_count'], a['abstain_count'],
                                               a['created_at'][:16]))
        load_rev()

        def view_amendment():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条草案'); return
            aid = int(tree.item(sel[0])['values'][0])
            stats = DB.amendment_stats(aid)
            a = stats['amendment']
            win = tk.Toplevel(self.root)
            win.title(f'修宪草案 - {a.get("title")}')
            win.geometry('720x560')
            win.transient(self.root); win.grab_set()
            tk.Label(win, text=f"《{a.get('title')}》　状态：{a.get('status')}　发起人：{a.get('proposer_name')}",
                     font=("Arial", 12, "bold")).pack(pady=8)
            tk.Label(win, text=f"赞成 {stats['yes']} / 反对 {stats['no']} / 弃权 {stats['abstain']}",
                     font=("Arial", 11), fg=COLOR_PURPLE).pack()
            tk.Label(win, text="条文内容：", font=("Arial", 11, "bold"), anchor='w').pack(anchor='w', padx=20)
            make_foldable_content(win, (a.get('content') or '').splitlines())
            tk.Label(win, text="投票明细：", font=("Arial", 11, "bold"), anchor='w').pack(anchor='w', padx=20)
            cols2 = ('id', '议员', '投票', '部门', '时间')
            tf2, tree2 = make_tree(win, cols2, {'id': 40, '议员': 90, '投票': 70,
                                                '部门': 120, '时间': 150}, height=7)
            tf2.pack(fill='both', expand=True, padx=20, pady=4)
            for v in stats['voters']:
                tree2.insert('', 'end', values=(v['id'], v['official_name'], v['vote'],
                                                v.get('department') or '-', v['voted_at'][:16]))
            btnf = tk.Frame(win); btnf.pack(pady=8)
            # 议员投票
            if is_leg:
                for label, vote in (('✅ 赞成', '赞成'), ('❌ 反对', '反对'), ('⏸️ 弃权', '弃权')):
                    make_flat_button(btnf, label,
                                     lambda v=vote: self._do_amendment_vote(aid, v, win),
                                     {'赞成': '#27ae60', '反对': '#e74c3c', '弃权': '#f39c12'}[vote],
                                     {'赞成': '#1e8449', '反对': '#c0392b', '弃权': '#d68910'}[vote],
                                     width=10).pack(side='left', padx=5)
            # 议长/管理员结束审议
            if is_speaker or is_admin:
                make_flat_button(btnf, '📌 结束审议并计票', lambda: self._do_close_amendment(aid, win),
                                 COLOR_PURPLE, COLOR_PURPLE, width=16).pack(side='left', padx=8)

        make_flat_button(rev, '👁️ 查看草案 / 投票', view_amendment,
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=18).pack(pady=6)

    def _do_amendment_vote(self, aid, vote, win):
        ok, msg = DB.cast_amendment_vote(aid, self.user['id'], self.user['name'], vote)
        messagebox.showinfo('投票结果', msg)
        win.destroy()
        self._show_constitution()

    def _do_close_amendment(self, aid, win):
        if not messagebox.askyesno('确认', '结束审议并计票？赞成 > 反对则修正案通过并自动生效。'):
            return
        ok, msg = DB.close_amendment(aid)
        if ok:
            DB.log(self.user['type'], self.user.get('id', ''), self.user['name'], '修宪审议结束', msg)
            messagebox.showinfo('审议结果', msg)
        else:
            messagebox.showwarning('审议结果', msg)
        win.destroy()
        self._show_constitution()

    # ========== 弹劾管理 ==========
    def _show_impeachment(self):
        self._clear_main()
        make_page_title(self.main_frame, "🚨 弹劾管理（议会表决 · 同意 ≥ 在职议员 2/3 通过 · 通过即撤职并交接）").pack(pady=15)
        conn = DB.get_conn(GOV_DB)
        pres = conn.execute("SELECT * FROM officials WHERE position='总统' AND status='在职'").fetchone()
        vp = conn.execute("SELECT * FROM officials WHERE position='副总统' AND status='在职'").fetchone()
        conn.close()
        is_leg = DB.is_legislator(self.user)
        is_admin = self.user['type'] == 'admin'

        top = tk.LabelFrame(self.main_frame, text="👑 现任国家元首", font=("Arial", 12, "bold"),
                            bg=COLOR_CONTENT_BG, padx=15, pady=8)
        top.pack(fill='x', padx=25)
        if pres:
            tk.Label(top, text=f"总统：{pres['name']}（{pres['rank']}）", font=("Arial", 12, "bold"),
                     bg=COLOR_CONTENT_BG, fg=COLOR_PURPLE).pack(anchor='w')
        else:
            tk.Label(top, text="总统：空缺", font=("Arial", 12, "bold"),
                     bg=COLOR_CONTENT_BG, fg=COLOR_WARNING).pack(anchor='w')
        if vp:
            tk.Label(top, text=f"副总统：{vp['name']}（宪法继任顺序第一位）",
                     font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#555').pack(anchor='w')
        tk.Label(top, text="⚖️ 宪法规则：总统被撤职/删除（非大选落选）时，副总统自动当选总统并交接；"
                           "弹劾案由议会表决，同意票达在职议员 2/3 即通过，通过后总统自动撤职并交接。",
                 font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#777', wraplength=1100,
                 justify='left').pack(anchor='w', pady=(4, 0))

        if (is_admin or is_leg) and pres:
            def do_initiate():
                win = tk.Toplevel(self.root)
                win.title('发起弹劾 - 弹劾总统')
                win.geometry('480x320')
                win.transient(self.root); win.grab_set()
                tk.Label(win, text=f"弹劾对象：{pres['name']}（现任总统）", font=("Arial", 12, "bold")).pack(pady=10)
                tk.Label(win, text="弹劾理由：").pack(anchor='w', padx=20)
                te = tk.Text(win, width=52, height=6, font=("Arial", 10))
                te.pack(padx=20, pady=4)
                def submit():
                    reason = te.get('1.0', 'end').strip()
                    if not reason:
                        messagebox.showwarning('提示', '请填写弹劾理由'); return
                    ok, msg, imp_id = DB.initiate_impeachment(
                        pres['id'], pres['name'], reason, self.user.get('id'), self.user['name'], is_admin)
                    if ok:
                        DB.log(self.user['type'], self.user.get('id', ''), self.user['name'], '发起弹劾', msg)
                        messagebox.showinfo('成功', msg)
                        win.destroy()
                        self._show_impeachment()
                    else:
                        messagebox.showerror('失败', msg)
                make_flat_button(win, '提交议会表决', submit, COLOR_DANGER, '#a93226', width=14).pack(pady=10)
            make_flat_button(top, '🚨 发起弹劾', do_initiate, COLOR_DANGER, '#a93226',
                             width=14, height=1).pack(anchor='w', pady=(8, 0))

        rev = tk.LabelFrame(self.main_frame, text="弹劾案列表", font=("Arial", 12, "bold"),
                            bg=COLOR_CONTENT_BG, padx=10, pady=8)
        rev.pack(fill='both', expand=True, padx=25, pady=10)
        cols = ('id', '被弹劾人', '理由', '状态', '赞成', '反对', '弃权', '发起人', '时间')
        tf, tree = make_tree(rev, cols, {'id': 40, '被弹劾人': 90, '理由': 300, '状态': 70,
                                         '赞成': 55, '反对': 55, '弃权': 55, '发起人': 80, '时间': 130}, height=8)
        tf.pack(fill='both', expand=True)

        def load_list():
            for it in tree.get_children():
                tree.delete(it)
            for imp in DB.list_impeachments():
                reason_short = (imp['reason'] or '').replace('\n', ' ')
                if len(reason_short) > 20:
                    reason_short = reason_short[:20] + '…'
                tree.insert('', 'end', values=(imp['id'], imp['target_name'], reason_short, imp['status'],
                                               imp['yes_count'], imp['no_count'], imp['abstain_count'],
                                               imp['initiator_name'], imp['created_at'][:16]))
        load_list()

        def view_impeachment():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条弹劾案'); return
            imp_id = int(tree.item(sel[0])['values'][0])
            st = DB.impeachment_stats(imp_id)
            imp = st['impeachment']
            win = tk.Toplevel(self.root)
            win.title(f'弹劾案 - {imp.get("target_name")}')
            win.geometry('720x540')
            win.transient(self.root); win.grab_set()
            tk.Label(win, text=f"弹劾 {imp.get('target_name')}　状态：{imp.get('status')}　发起人：{imp.get('initiator_name')}",
                     font=("Arial", 12, "bold")).pack(pady=8)
            tk.Label(win, text=f"赞成 {st['yes']} / 反对 {st['no']} / 弃权 {st['abstain']}"
                               f"　（在职议员 {st['total_leg']} 人，通过需 {(-(-st['total_leg'] * 2 // 3)) if st['total_leg'] else 0} 票）",
                     font=("Arial", 11), fg=COLOR_DANGER).pack()
            tk.Label(win, text="弹劾理由：", font=("Arial", 11, "bold"), anchor='w').pack(anchor='w', padx=20)
            tk.Label(win, text=imp.get('reason') or '', font=("Arial", 10), fg='#333',
                     justify='left', anchor='w', wraplength=660, bg='#f7f7f9').pack(fill='x', padx=20, pady=4)
            tk.Label(win, text="表决明细：", font=("Arial", 11, "bold"), anchor='w').pack(anchor='w', padx=20)
            cols2 = ('id', '议员', '表决', '部门', '时间')
            tf2, tree2 = make_tree(win, cols2, {'id': 40, '议员': 90, '表决': 70,
                                                '部门': 140, '时间': 150}, height=6)
            tf2.pack(fill='both', expand=True, padx=20, pady=4)
            for v in st['voters']:
                tree2.insert('', 'end', values=(v['id'], v['official_name'], v['vote'],
                                                v.get('department') or '-', v['voted_at'][:16]))
            btnf = tk.Frame(win); btnf.pack(pady=8)
            if is_leg and imp.get('status') == '进行中':
                for label, vote in (('✅ 赞成（撤职）', '赞成'), ('❌ 反对', '反对'), ('⏸️ 弃权', '弃权')):
                    make_flat_button(btnf, label,
                                     lambda v=vote: self._do_impeachment_vote(imp_id, v, win),
                                     {'赞成': '#e74c3c', '反对': '#27ae60', '弃权': '#f39c12'}[vote],
                                     {'赞成': '#c0392b', '反对': '#1e8449', '弃权': '#d68910'}[vote],
                                     width=12).pack(side='left', padx=5)
            if (is_admin or (is_leg and self.user.get('position') in ('参议院议长', '众议院议长'))) \
                    and imp.get('status') == '进行中':
                make_flat_button(btnf, '📌 结束表决并计票', lambda: self._do_close_impeachment(imp_id, win),
                                 COLOR_PURPLE, COLOR_PURPLE, width=16).pack(side='left', padx=8)

        make_flat_button(rev, '👁️ 查看弹劾案 / 表决', view_impeachment,
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=18).pack(pady=6)

    def _do_impeachment_vote(self, imp_id, vote, win):
        ok, msg = DB.cast_impeachment_vote(imp_id, self.user['id'], self.user['name'], vote)
        messagebox.showinfo('表决结果', msg)
        win.destroy()
        self._show_impeachment()

    def _do_close_impeachment(self, imp_id, win):
        if not messagebox.askyesno('确认', '结束弹劾表决并计票？同意票达在职议员 2/3 则通过：'
                                         '总统自动撤职并由副总统交接。'):
            return
        ok, msg = DB.close_impeachment(imp_id)
        if ok:
            DB.log(self.user['type'], self.user.get('id', ''), self.user['name'], '弹劾审议结束', msg)
            messagebox.showinfo('弹劾结果', msg)
        else:
            messagebox.showwarning('弹劾结果', msg)
        win.destroy()
        self._show_impeachment()

    # ========== 总统竞选自荐申请（官员端 + 普通用户端） ==========
    def _show_candidacy_apply(self):
        self._clear_main()
        make_page_title(self.main_frame, "🗳️ 申请参加总统竞选（填写申请书 → 提交后台审核）").pack(pady=15)

        conn = DB.get_conn(GOV_DB)
        elec = conn.execute("SELECT * FROM elections WHERE status IN ('候选人登记','投票中') "
                            "AND election_type='regular' ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        form = tk.LabelFrame(self.main_frame, text="参选申请书", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=15, pady=8)
        form.pack(fill='x', padx=25)
        if not elec:
            tk.Label(form, text="当前没有可申请的大选（大选开启后至10月28日前可申请参选）。",
                     font=("Arial", 11), bg=COLOR_CONTENT_BG, fg=COLOR_WARNING).pack(anchor='w', pady=4)
        else:
            tk.Label(form, text=f"当前大选：{elec['title']}（{elec['election_type']}）· {elec['status']}阶段"
                                f"（申请截止：{int(str(elec['created_at'])[:4])}-10-28）",
                     font=("Arial", 11, "bold"), bg=COLOR_CONTENT_BG, fg=COLOR_PURPLE).pack(anchor='w')
        tk.Label(form, text="竞选党派（可选，无党派可作独立候选人）：", font=("Arial", 10),
                 bg=COLOR_CONTENT_BG).pack(anchor='w', pady=(8, 0))
        parties = DB.list_parties(active_only=True)
        pv = tk.StringVar(value='独立候选人')
        opts = ['独立候选人'] + [p['name'] for p in parties]
        ttk.Combobox(form, textvariable=pv, values=opts, state='readonly', width=24,
                     font=("Arial", 10)).pack(anchor='w')
        tk.Label(form, text="竞选申请书（介绍您的政见、履历与施政纲领）：", font=("Arial", 10),
                 bg=COLOR_CONTENT_BG).pack(anchor='w', pady=(8, 0))
        te = tk.Text(form, width=90, height=7, font=("Arial", 10))
        te.pack(anchor='w', pady=4)

        tk.Label(form, text="副总统搭档（身份证号码 + 姓名，须户籍核对；当选后共同就职）：", font=("Arial", 10),
                 bg=COLOR_CONTENT_BG).pack(anchor='w', pady=(8, 0))
        pf_p = tk.Frame(form, bg=COLOR_CONTENT_BG); pf_p.pack(anchor='w', pady=2)
        p_idn_e = make_groove_entry(pf_p, width=24); p_idn_e.pack(side='left', padx=2)
        p_name_e = make_groove_entry(pf_p, width=14); p_name_e.pack(side='left', padx=2)

        def do_submit():
            if not elec:
                messagebox.showwarning('提示', '当前没有可申请的大选'); return
            platform = te.get('1.0', 'end').strip()
            if not platform:
                messagebox.showwarning('提示', '请填写竞选申请书'); return
            pname = pv.get()
            pid = next((p['id'] for p in parties if p['name'] == pname), 0)
            ok, msg, aid = DB.submit_candidacy(self.user, platform, pid, pname if pid else '',
                                               p_idn_e.get().strip(), p_name_e.get().strip())
            if ok:
                DB.log(self.user['type'], self.user.get('id', ''), self.user['name'], '提交参选申请', msg)
                messagebox.showinfo('成功', msg)
                te.delete('1.0', 'end')
                p_idn_e.delete(0, 'end'); p_name_e.delete(0, 'end')
                self._show_candidacy_apply()
            else:
                messagebox.showwarning('提示', msg)
        if elec:
            make_flat_button(form, '📨 提交参选申请', do_submit, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=16).pack(anchor='w', pady=6)

        rec = tk.LabelFrame(self.main_frame, text="我的参选申请记录", font=("Arial", 12, "bold"),
                            bg=COLOR_CONTENT_BG, padx=10, pady=8)
        rec.pack(fill='both', expand=True, padx=25, pady=10)
        cols = ('id', '姓名', '党派', '副总统搭档', '状态', '申请书', '时间')
        tf, tree = make_tree(rec, cols, {'id': 40, '姓名': 80, '党派': 100, '副总统搭档': 100,
                                         '状态': 80, '申请书': 340, '时间': 130}, height=6)
        tf.pack(fill='both', expand=True)
        for r in DB.my_candidacy_applications(self.user):
            plat = (r['platform'] or '').replace('\n', ' ')
            if len(plat) > 34:
                plat = plat[:34] + '…'
            tree.insert('', 'end', values=(r['id'], r['name'], r['party_name'] or '独立候选人',
                                           r['partner_name'] or '-', r['status'], plat,
                                           r['created_at'][:16]))

    # ========== 参选审核（议会议长权限） ==========
    def _show_candidacy_review(self):
        self._clear_main()
        _pos = (self.user.get('position') or '') if self.user.get('type') == 'official' else ''
        if not (self.user.get('type') == 'admin' or DB.is_super_user(self.user)
                or _pos in ('参议院议长', '众议院议长', '总统')):
            tk.Label(self.main_frame, text="⚠️ 参选审核权属于袁泓涵与系统管理员（议会议长亦可）",
                     font=("Arial", 14), bg=COLOR_CONTENT_BG, fg=COLOR_DANGER).pack(pady=60)
            return
        make_page_title(self.main_frame, "📥 总统竞选参选审核（议会议长）").pack(pady=15)
        st_var = tk.StringVar(value='待审核')
        f_f = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        f_f.pack(pady=5)
        tk.Label(f_f, text="状态：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left')
        ttk.Combobox(f_f, textvariable=st_var, values=['待审核', '已通过', '已拒绝', '全部'],
                     width=10, state='readonly', font=("Arial", 10)).pack(side='left', padx=5)

        lst = tk.LabelFrame(self.main_frame, text="参选申请列表", font=("Arial", 12, "bold"),
                            bg=COLOR_CONTENT_BG, padx=10, pady=8)
        lst.pack(fill='both', expand=True, padx=25, pady=8)
        cols = ('id', '类型', '姓名', '身份证号', '党派', '副总统搭档', '状态', '申请书', '提交时间')
        tf, tree = make_tree(lst, cols, {'id': 40, '类型': 50, '姓名': 80, '身份证号': 140, '党派': 90,
                                         '副总统搭档': 100, '状态': 70, '申请书': 300, '提交时间': 130}, height=9)
        tf.pack(fill='both', expand=True)

        def load():
            for it in tree.get_children():
                tree.delete(it)
            st = st_var.get()
            for r in DB.list_candidacy_applications(None if st == '全部' else st):
                plat = (r['platform'] or '').replace('\n', ' ')
                if len(plat) > 30:
                    plat = plat[:30] + '…'
                kind = '官员' if r['applicant_type'] == 'official' else '公民'
                tree.insert('', 'end', values=(r['id'], kind, r['name'], r['id_number'],
                                               r['party_name'] or '独立候选人', r['partner_name'] or '-',
                                               r['status'], plat, r['created_at'][:16]))
        load()
        st_var.trace_add('write', lambda *_: load())

        def get_sel():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一条申请'); return None
            return int(tree.item(sel[0])['values'][0])

        def view_apply():
            rid = get_sel()
            if not rid:
                return
            r = next((x for x in DB.list_candidacy_applications() if x['id'] == rid), None)
            if not r:
                return
            win = tk.Toplevel(self.root)
            win.title(f'参选申请 - {r["name"]}')
            win.geometry('640x460')
            win.transient(self.root); win.grab_set()
            info = [('姓名', r['name']), ('身份证号', r['id_number']),
                    ('类型', '官员' if r['applicant_type'] == 'official' else '公民'),
                    ('党派', r['party_name'] or '独立候选人'), ('状态', r['status']),
                    ('提交时间', r['created_at'][:16])]
            for i, (k, v) in enumerate(info):
                tk.Label(win, text=f'{k}：', font=("Arial", 10, "bold"), anchor='e').grid(
                    row=i, column=0, sticky='e', padx=10, pady=4)
                tk.Label(win, text=str(v), font=("Arial", 10), anchor='w').grid(
                    row=i, column=1, sticky='w')
            tk.Label(win, text='竞选申请书：', font=("Arial", 11, "bold"), anchor='w').grid(
                row=6, column=0, columnspan=2, sticky='w', padx=10, pady=(10, 2))
            tk.Label(win, text=r['platform'] or '', font=("Arial", 10), justify='left', anchor='nw',
                     wraplength=580, bg='#f7f7f9').grid(
                row=7, column=0, columnspan=2, sticky='nsew', padx=10, pady=4)
            win.rowconfigure(7, weight=1)
            win.columnconfigure(0, weight=1)
            win.columnconfigure(1, weight=1)
            if r['status'] == '待审核':
                bf = tk.Frame(win); bf.grid(row=8, column=0, columnspan=2, pady=10)
                make_flat_button(bf, '✅ 通过并加入候选名单',
                                 lambda: self._do_review_candidacy(rid, True, win),
                                 COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=18).pack(side='left', padx=6)
                make_flat_button(bf, '❌ 拒绝',
                                 lambda: self._do_review_candidacy(rid, False, win),
                                 COLOR_DANGER, '#a93226', width=12).pack(side='left', padx=6)

        make_flat_button(lst, '👁️ 查看申请书 / 审核', view_apply, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                         width=20).pack(pady=6)

    def _do_review_candidacy(self, rid, approve, win):
        _pos = (self.user.get('position') or '') if self.user.get('type') == 'official' else ''
        if self.user.get('type') != 'admin' and _pos not in ('参议院议长', '众议院议长'):
            messagebox.showwarning('无权操作', '参选审核权属于议会议长')
            return
        ok, msg = DB.review_candidacy(rid, approve, self.user['name'])
        if ok:
            DB.log(self.user.get('type', 'admin'), self.user['id'], self.user['name'],
                   '参选审核' + ('通过' if approve else '拒绝'), msg)
            messagebox.showinfo('审核结果', msg)
        else:
            messagebox.showwarning('审核结果', msg)
        win.destroy()
        self._show_candidacy_review()

    # ========== 公民端：入党申请 ==========
    def _show_party_apply(self):
        self._clear_main()
        make_page_title(self.main_frame, "📨 入党申请（选择党派 → 提交审核）").pack(pady=15)
        form = tk.LabelFrame(self.main_frame, text="申请入党", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=15, pady=8)
        form.pack(fill='x', padx=25)
        parties = DB.list_parties(active_only=True)
        if not parties:
            tk.Label(form, text="当前没有可加入的党派。", font=("Arial", 11),
                     bg=COLOR_CONTENT_BG, fg=COLOR_WARNING).pack(anchor='w')
        else:
            pv = tk.StringVar(value=parties[0]['name'])
            tk.Label(form, text="选择党派：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(anchor='w')
            ttk.Combobox(form, textvariable=pv, values=[p['name'] for p in parties],
                         state='readonly', width=24, font=("Arial", 10)).pack(anchor='w', pady=4)
            tk.Label(form, text=f"申请人：{self.user['name']}（{self.user['id_number']}）",
                     font=("Arial", 10), bg=COLOR_CONTENT_BG, fg='#555').pack(anchor='w')

            def do_apply():
                pid = next((p['id'] for p in parties if p['name'] == pv.get()), 0)
                ok, msg = DB.apply_party_citizen(pid, self.user['id_number'], self.user['name'])
                if ok:
                    DB.log('citizen', self.user['id_number'], self.user['name'], '提交入党申请', msg)
                    messagebox.showinfo('成功', msg)
                    self._show_party_apply()
                else:
                    messagebox.showwarning('提示', msg)
            make_flat_button(form, '📨 提交入党申请', do_apply, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=16).pack(anchor='w', pady=8)
        rec = tk.LabelFrame(self.main_frame, text="我的入党申请记录", font=("Arial", 12, "bold"),
                            bg=COLOR_CONTENT_BG, padx=10, pady=8)
        rec.pack(fill='both', expand=True, padx=25, pady=10)
        cols = ('id', '党派', '状态', '申请时间')
        tf, tree = make_tree(rec, cols, {'id': 40, '党派': 160, '状态': 90, '申请时间': 160}, height=6)
        tf.pack(fill='both', expand=True)
        for r in DB.my_party_requests_citizen(self.user['id_number']):
            tree.insert('', 'end', values=(r['id'], r['party_name'], r['status'], r['created_at'][:16]))

    # ========== 党派中心（官员端 · 党派门户） ==========
    def _show_party_portal(self):
        self._clear_main()
        make_page_title(self.main_frame, "🏛️ 党派中心（党派门户 · 候选人 · 支持率）").pack(pady=15)

        my = DB.official_party(self.user['id'])
        is_leader = bool(my and my['leader_id'] == self.user['id'])

        # ---- 顶部：党派信息卡 ----
        info = tk.LabelFrame(self.main_frame, text="我的党派", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=15, pady=8)
        info.pack(fill='x', padx=25, pady=6)
        info_txt = tk.Label(info, font=("Arial", 11), bg=COLOR_CONTENT_BG, anchor='w', justify='left')
        info_txt.pack(fill='x')

        # ---- 左侧：党员管理 ----
        left = tk.LabelFrame(self.main_frame, text="党员管理（本党派）", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=10, pady=8)
        left.pack(side='left', fill='both', expand=True, padx=(25, 10), pady=8)
        cols = ('id', '姓名', '部门', '职务', '职级', '入党时间')
        tf, tree = make_tree(left, cols, {'id': 40, '姓名': 90, '部门': 110,
                                          '职务': 110, '职级': 110, '入党时间': 130}, height=8)
        tf.pack(fill='both', expand=True)

        # ---- 右侧：选举代表人 + 支持率 ----
        right = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        right.pack(side='left', fill='both', expand=True, padx=(10, 25), pady=8)

        cand_f = tk.LabelFrame(right, text="选举代表人（本次大选）", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=8)
        cand_f.pack(fill='x')
        cand_cols = ('id', '候选人', '党派', '竞选纲领', '得票')
        cand_tf, cand_tree = make_tree(cand_f, cand_cols, {'id': 40, '候选人': 90, '党派': 90,
                                                           '竞选纲领': 200, '得票': 60}, height=4)
        cand_tf.pack(fill='x')

        sup_f = tk.LabelFrame(right, text="实时支持率看板", font=("Arial", 12, "bold"),
                              bg=COLOR_CONTENT_BG, padx=10, pady=8)
        sup_f.pack(fill='both', expand=True, pady=(8, 0))
        sup_meta = tk.Label(sup_f, font=("Arial", 10, 'bold'), bg=COLOR_CONTENT_BG,
                            fg=COLOR_TITLE, anchor='w')
        sup_meta.pack(fill='x')
        sup_body = tk.Frame(sup_f, bg=COLOR_CONTENT_BG)
        sup_body.pack(fill='both', expand=True)

        def refresh_support():
            for w in sup_body.winfo_children():
                w.destroy()
            eid = None
            for e in DB.list_elections():
                if e['status'] in ('候选人登记', '投票中'):
                    eid = e['id']
                    break
            if not eid:
                sup_meta.config(text='当前没有进行中的大选')
                return
            data = DB.election_support_rates(eid)
            e = next((x for x in DB.list_elections() if x['id'] == eid), None)
            sup_meta.config(text=f"大选《{e['title']}》· 状态：{e['status']}　已投票 {data['voted']}/{data['residents']} 人（投票率 {data['turnout']}%）")
            if not data['candidates']:
                tk.Label(sup_body, text='暂无候选人', bg=COLOR_CONTENT_BG, font=("Arial", 10)).pack(anchor='w', pady=6)
                return
            maxv = max(data['candidates'], key=lambda x: x['votes'])['votes'] or 1
            for cnd in data['candidates']:
                row = tk.Frame(sup_body, bg=COLOR_CONTENT_BG)
                row.pack(fill='x', pady=3)
                txt = f"{cnd['official_name']}（{cnd['party_name'] or '无党派'}）　{cnd['votes']} 票　支持率 {cnd['support_rate']}%"
                lab = tk.Label(row, text=txt, font=("Arial", 10), bg=COLOR_CONTENT_BG, anchor='w')
                lab.pack(fill='x')
                bar = tk.Canvas(row, height=16, bg='#e8e8e8', highlightthickness=0)
                bar.pack(fill='x')
                w = int(bar.winfo_width() * cnd['votes'] / maxv) if bar.winfo_width() > 10 else 0
                color = COLOR_PURPLE if (my and cnd['party_name'] == my['name']) else '#4a90d9'
                bar.create_rectangle(0, 0, max(w, 4), 16, fill=color, outline='')

        def refresh_candidates():
            for it in cand_tree.get_children():
                cand_tree.delete(it)
            for e in DB.list_elections():
                if e['status'] in ('候选人登记', '投票中'):
                    for cnd in DB.election_candidates(e['id']):
                        cand_tree.insert('', 'end', values=(cnd['id'], cnd['official_name'],
                                                            cnd['party_name'] or '-',
                                                            (cnd['platform'] or '')[:24], cnd['votes']))
                    break

        def refresh_members():
            for it in tree.get_children():
                tree.delete(it)
            if not my:
                return
            for m in DB.party_members(my['id']):
                tree.insert('', 'end', values=(m['official_id'], m['official_name'],
                                               m['department'] or '-', m['position'] or '-',
                                               m['rank'] or '-', m['joined_at']))

        def refresh_all():
            nonlocal my, is_leader
            my = DB.official_party(self.user['id'])
            is_leader = bool(my and my['leader_id'] == self.user['id'])
            if my:
                mems = DB.party_members(my['id'])
                leader_name = ''
                for p in DB.list_parties():
                    if p['id'] == my['id']:
                        leader_name = p['leader_name'] or ''
                        break
                info_txt.config(text=f"党派：《{my['name']}》（{my['short_name'] or '无简称'}）　"
                                     f"党魁：{leader_name}　党员：{len(mems)} 人　您的身份：{'党魁' if is_leader else '党员'}")
            else:
                info_txt.config(text='您尚未加入任何党派。请在下方“申请入党”中选择党派。')
            refresh_members()
            refresh_candidates()
            refresh_support()
            show_apply_area()
            show_leader_area()

        # ---- 申请入党区（未入党时显示） ----
        apply_box = tk.LabelFrame(self.main_frame, text="申请入党", font=("Arial", 11, "bold"),
                                  bg=COLOR_CONTENT_BG, padx=12, pady=8)
        apply_box.pack(fill='x', padx=25, pady=6)

        def show_apply_area():
            for w in apply_box.winfo_children():
                w.destroy()
            if my:
                tk.Label(apply_box, text='✅ 您已是本党成员。', font=("Arial", 11),
                         bg=COLOR_CONTENT_BG, fg=COLOR_SUCCESS).pack(anchor='w', pady=4)
                if is_leader:
                    tk.Label(apply_box, text='您拥有党魁权限：可审批入党申请、移除党员、交接党魁、推举/撤回候选人。',
                             font=("Arial", 10), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(anchor='w')
                return
            parties = DB.list_parties()
            if not parties:
                tk.Label(apply_box, text='暂无党派可加入。', font=("Arial", 10),
                         bg=COLOR_CONTENT_BG).pack(anchor='w')
                return
            tk.Label(apply_box, text='选择党派：', font=("Arial", 10), bg=COLOR_CONTENT_BG).pack(side='left')
            var = tk.StringVar(value=parties[0]['name'])
            ttk.Combobox(apply_box, textvariable=var, values=[p['name'] for p in parties],
                         font=("Arial", 10), width=18, state='readonly').pack(side='left', padx=6)
            note_e = make_groove_entry(apply_box, width=30)
            note_e.pack(side='left', padx=6)
            tk.Label(apply_box, text='申请理由', font=("Arial", 9), bg=COLOR_CONTENT_BG,
                     fg=COLOR_TEXT_GRAY).pack(side='left')
            def do_apply():
                target = next((p for p in parties if p['name'] == var.get()), None)
                if not target:
                    return
                ok, msg = DB.apply_party(target['id'], {'id': self.user['id'], 'name': self.user['name']})
                if ok:
                    DB.log(self.user['type'], self.user['id'], self.user['name'], '申请入党',
                           f'申请加入《{target["name"]}》')
                messagebox.showinfo('结果', msg)
                refresh_all()
            make_flat_button(apply_box, '提交申请', do_apply, COLOR_PRIMARY, COLOR_PRIMARY_HOVER,
                             width=10).pack(side='left', padx=8)

        # ---- 党魁专属操作区 ----
        leader_box = tk.LabelFrame(self.main_frame, text="党魁专属（党务管理）", font=("Arial", 11, "bold"),
                                   bg=COLOR_CONTENT_BG, padx=12, pady=8)
        leader_box.pack(fill='x', padx=25, pady=6)

        def show_leader_area():
            for w in leader_box.winfo_children():
                w.destroy()
            if not (my and is_leader):
                return
            row = tk.Frame(leader_box, bg=COLOR_CONTENT_BG); row.pack(fill='x', pady=2)
            pend = DB.pending_party_requests(my['id'])
            if pend:
                tk.Label(row, text=f"待审批入党申请 {len(pend)} 条：", font=("Arial", 10, 'bold'),
                         bg=COLOR_CONTENT_BG).pack(anchor='w')
                for r in pend:
                    rw = tk.Frame(leader_box, bg=COLOR_CONTENT_BG); rw.pack(fill='x', pady=2)
                    tk.Label(rw, text=f"{r['official_name']}（{r['department'] or '-'}·{r['position'] or '-'}）",
                             font=("Arial", 10), bg=COLOR_CONTENT_BG).pack(side='left', padx=4)
                    make_flat_button(rw, '批准', lambda rid=r['id']: self._decide_req(rid, True, refresh_all),
                                     COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=6).pack(side='left', padx=4)
                    make_flat_button(rw, '拒绝', lambda rid=r['id']: self._decide_req(rid, False, refresh_all),
                                     COLOR_DANGER, COLOR_DANGER_HOVER, width=6).pack(side='left', padx=4)
            else:
                tk.Label(row, text='暂无待审批的入党申请。', font=("Arial", 10),
                         bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(anchor='w')

            r2 = tk.Frame(leader_box, bg=COLOR_CONTENT_BG); r2.pack(fill='x', pady=(6, 2))
            make_flat_button(r2, '➖ 移除党员', lambda: self._party_kick(my, tree, refresh_all),
                             COLOR_DANGER, COLOR_DANGER_HOVER, width=12).pack(side='left', padx=4)
            make_flat_button(r2, '👑 交接党魁', lambda: self._party_handover(my, tree, refresh_all),
                             COLOR_WARNING, COLOR_WARNING, width=12).pack(side='left', padx=4)
            make_flat_button(r2, '🗳️ 推举候选人', lambda: self._party_nominate(my, refresh_all),
                             COLOR_PURPLE, COLOR_PURPLE, width=12).pack(side='left', padx=4)
            make_flat_button(r2, '↩️ 撤回候选人', lambda: self._party_withdraw(my, cand_tree, refresh_all),
                             COLOR_GRAY, COLOR_GRAY, width=12).pack(side='left', padx=4)

        btns = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btns.pack(pady=6)
        make_flat_button(btns, "🔄 刷新", refresh_all, COLOR_GRAY, COLOR_GRAY, width=10).pack(side='left', padx=6)
        make_flat_button(btns, "🗳️ 前往大选中心", self._show_election_center,
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14).pack(side='left', padx=6)
        refresh_all()

    def _decide_req(self, req_id, approve, refresh_all):
        if approve:
            ok, msg = DB.approve_party_request(req_id, self.user['name'])
        else:
            ok, msg = DB.reject_party_request(req_id, self.user['name'])
        if ok:
            DB.log(self.user['type'], self.user['id'], self.user['name'],
                   '入党审批', msg)
        messagebox.showinfo('结果', msg)
        refresh_all()

    def _party_kick(self, my, tree, refresh_all):
        sel = tree.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在党员列表中选择一名党员'); return
        v = tree.item(sel[0])['values']
        if int(v[0]) == self.user['id']:
            messagebox.showwarning('提示', '不能移除自己（党魁）'); return
        if not messagebox.askyesno('确认', f'确定将 {v[1]} 移出本党吗？'):
            return
        ok, msg = DB.remove_party_member(my['id'], int(v[0]))
        if ok:
            DB.log(self.user['type'], self.user['id'], self.user['name'], '移除党员', f'移出{v[1]}')
        messagebox.showinfo('结果', msg)
        refresh_all()

    def _party_handover(self, my, tree, refresh_all):
        sel = tree.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在党员列表中选择接任党魁的党员'); return
        v = tree.item(sel[0])['values']
        if int(v[0]) == self.user['id']:
            messagebox.showwarning('提示', '党魁不能交接给自己'); return
        if not messagebox.askyesno('确认', f'确定将党魁交接给 {v[1]} 吗？'):
            return
        ok, msg = DB.set_party_leader(my['id'], int(v[0]), v[1])
        if ok:
            DB.log(self.user['type'], self.user['id'], self.user['name'], '党魁交接', f'交接给{v[1]}')
        messagebox.showinfo('结果', msg)
        refresh_all()

    def _party_nominate(self, my, refresh_all):
        eid = None
        for e in DB.list_elections():
            if e['election_type'] == 'regular' and e['status'] in ('候选人登记', '投票中'):
                eid = e['id']
                break
        if not eid:
            messagebox.showwarning('提示', '当前没有可推举的大选（大选开启后至10月28日前可登记）'); return
        mems = DB.party_members(my['id'])
        if not mems:
            messagebox.showwarning('提示', '本党暂无党员可推举'); return
        win = tk.Toplevel(self.root)
        win.title('推举本党候选人'); win.geometry('620x500')
        win.transient(self.root); win.grab_set()
        tk.Label(win, text=f"《{my['name']}》推举大选候选人（每党可推举 1~2 名）",
                 font=("Arial", 11, "bold")).pack(pady=6)
        cols = ('id', '姓名', '部门', '职务')
        tf, tree = make_tree(win, cols, {'id': 50, '姓名': 90, '部门': 150, '职务': 140}, height=8)
        tf.pack(fill='both', expand=True, padx=12)
        for m in mems:
            tree.insert('', 'end', values=(m['official_id'], m['official_name'],
                                           m['department'] or '-', m['position'] or '-'))
        plf = tk.Frame(win); plf.pack(fill='x', padx=12, pady=6)
        tk.Label(plf, text='竞选纲领：').pack(side='left')
        pe = make_groove_entry(plf, width=34); pe.pack(side='left', padx=4)
        pfl = tk.Frame(win); pfl.pack(fill='x', padx=12, pady=2)
        tk.Label(pfl, text='副总统搭档身份证：').pack(side='left')
        p_idn_e = make_groove_entry(pfl, width=22); p_idn_e.pack(side='left', padx=4)
        tk.Label(pfl, text='姓名：').pack(side='left')
        p_name_e = make_groove_entry(pfl, width=12); p_name_e.pack(side='left', padx=4)
        tk.Label(win, text='（搭档须户籍核对，当选后次年1月20日共同就职）', font=("Arial", 9),
                 fg='#888888').pack(anchor='w', padx=14)
        def submit():
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请选择一名党员'); return
            v = tree.item(s[0])['values']
            ok, msg, cid = DB.nominate_candidate(eid, my['id'], my['name'],
                                                 {'id': int(v[0]), 'name': v[1]},
                                                 pe.get().strip(), p_idn_e.get().strip(), p_name_e.get().strip())
            if ok:
                DB.log(self.user['type'], self.user['id'], self.user['name'],
                       '推举候选人', f'推举{v[1]}参加大选{eid}')
                messagebox.showinfo('成功', msg)
                win.destroy(); refresh_all()
            else:
                messagebox.showerror('失败', msg)
        make_flat_button(win, '确认推举', submit, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=14).pack(pady=8)

    def _party_withdraw(self, my, cand_tree, refresh_all):
        sel = cand_tree.selection()
        if not sel:
            messagebox.showwarning('提示', '请先在候选人列表中选中要撤回的候选人'); return
        v = cand_tree.item(sel[0])['values']
        eid = None
        for e in DB.list_elections():
            if e['status'] == '候选人登记':
                eid = e['id']
                break
        if not eid:
            messagebox.showwarning('提示', '当前没有处于“候选人登记”阶段的大选'); return
        if not messagebox.askyesno('确认', f'确定撤回候选人 {v[1]} 吗？'):
            return
        ok, msg = DB.withdraw_candidate(eid, int(v[0]))
        if ok:
            DB.log(self.user['type'], self.user['id'], self.user['name'],
                   '撤回候选人', f'撤回{v[1]}')
        messagebox.showinfo('结果', msg)
        refresh_all()

    # ========== 党派管理 ==========
    def _show_party_mgmt(self):
        self._clear_main()
        make_page_title(self.main_frame, "🎭 党派管理（政党登记 · 党员 · 候选人）").pack(pady=15)

        # 左：建党表单
        left = tk.LabelFrame(self.main_frame, text="创建党派", font=("Arial", 12, "bold"),
                             bg=COLOR_CONTENT_BG, padx=15, pady=12)
        left.pack(side='left', fill='y', padx=(25, 10), pady=10)
        tk.Label(left, text="党派名称：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=0, column=0, pady=5, sticky='e')
        name_e = make_groove_entry(left, width=20); name_e.grid(row=0, column=1, pady=5)
        tk.Label(left, text="简称：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=1, column=0, pady=5, sticky='e')
        short_e = make_groove_entry(left, width=20); short_e.grid(row=1, column=1, pady=5)
        tk.Label(left, text="党魁（官员id）：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=2, column=0, pady=5, sticky='e')
        leader_e = make_groove_entry(left, width=20); leader_e.grid(row=2, column=1, pady=5)
        tk.Label(left, text="政治纲领：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(row=3, column=0, pady=5, sticky='ne')
        platform_t = tk.Text(left, width=22, height=4, font=("Arial", 10)); platform_t.grid(row=3, column=1, pady=5)

        def do_create():
            nm = name_e.get().strip()
            if not nm:
                messagebox.showwarning('提示', '请填写党派名称'); return
            lid = None; lname = ''
            try:
                lid = int(leader_e.get().strip() or 0)
            except ValueError:
                messagebox.showwarning('提示', '党魁ID必须是数字'); return
            if lid:
                conn = DB.get_conn(GOV_DB)
                r = conn.execute("SELECT name FROM officials WHERE id=?", (lid,)).fetchone()
                conn.close()
                if not r:
                    messagebox.showwarning('提示', '党魁ID不存在，请先在官员任免中查看ID'); return
                lname = r['name']
            ok, msg, pid = DB.create_party(nm, short_e.get().strip(), lid or None, lname, platform_t.get('1.0', 'end').strip())
            if ok:
                DB.log(self.user['type'], self.user.get('id', ''), self.user['name'], '建党', f'创建党派《{nm}》')
                messagebox.showinfo('成功', msg)
                self._show_party_mgmt()
            else:
                messagebox.showerror('失败', msg)
        make_flat_button(left, '确认建党', do_create, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=14).grid(
            row=4, column=0, columnspan=2, pady=12)

        # 右：党派列表 + 党员
        right = tk.LabelFrame(self.main_frame, text="党派列表（点击查看党员）", font=("Arial", 12, "bold"),
                              bg=COLOR_CONTENT_BG, padx=10, pady=8)
        right.pack(side='left', fill='both', expand=True, padx=(10, 25), pady=10)
        cols = ('id', '党派名称', '简称', '党魁', '纲领', '状态')
        tf, tree = make_tree(right, cols, {'id': 40, '党派名称': 130, '简称': 70,
                                           '党魁': 90, '纲领': 180, '状态': 60}, height=8)
        tf.pack(fill='both', expand=True)

        def load_parties():
            for it in tree.get_children():
                tree.delete(it)
            for p in DB.list_parties():
                tree.insert('', 'end', values=(p['id'], p['name'], p['short_name'],
                                               p['leader_name'] or '-',
                                               (p['platform'] or '')[:20], '启用' if p['is_active'] else '停用'))
        load_parties()

        def view_members():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一个党派'); return
            vals = tree.item(sel[0])['values']
            pid = int(vals[0])
            win = tk.Toplevel(self.root)
            win.title(f'党员管理 - {vals[1]}')
            win.geometry('640x460')
            win.transient(self.root); win.grab_set()
            tk.Label(win, text=f"《{vals[1]}》党员管理　党魁：{vals[3]}", font=("Arial", 12, "bold")).pack(pady=8)
            cols2 = ('id', '党员ID', '姓名', '部门', '职务', '入党时间')
            tf2, tree2 = make_tree(win, cols2, {'id': 40, '党员ID': 80, '姓名': 90,
                                                '部门': 130, '职务': 120, '入党时间': 140}, height=8)
            tf2.pack(fill='both', expand=True, padx=15, pady=5)

            def load_m():
                for it in tree2.get_children():
                    tree2.delete(it)
                for m in DB.party_members(pid):
                    tree2.insert('', 'end', values=(m['id'], m['official_id'], m['official_name'],
                                                    m['department'] or '-', m['position'] or '-', m['joined_at']))
            load_m()

            def add_member():
                conn = DB.get_conn(GOV_DB)
                rows = conn.execute("SELECT id,name,department,position FROM officials WHERE status='在职' "
                                    "ORDER BY CASE WHEN position='副部长' THEN 32 "
                                    "ELSE COALESCE((SELECT sort_order FROM positions WHERE name=officials.position),999) END, "
                                    "department, id").fetchall()
                conn.close()
                if not rows:
                    messagebox.showinfo('提示', '暂无在职官员'); return
                dwin = tk.Toplevel(win)
                dwin.title('选择党员'); dwin.geometry('520x400')
                dwin.transient(win); dwin.grab_set()
                tk.Label(dwin, text="选择在职官员加入党派：", font=("Arial", 11, "bold")).pack(pady=6)
                lf = tk.Frame(dwin); lf.pack(fill='both', expand=True, padx=12)
                cols3 = ('id', '姓名', '部门', '职务')
                tf3, tree3 = make_tree(lf, cols3, {'id': 50, '姓名': 90, '部门': 150, '职务': 140}, height=10)
                tf3.pack(fill='both', expand=True)
                for r in rows:
                    tree3.insert('', 'end', values=(r['id'], r['name'], r['department'], r['position']))
                def pick():
                    s = tree3.selection()
                    if not s:
                        messagebox.showwarning('提示', '请选择一名官员'); return
                    v = tree3.item(s[0])['values']
                    ok, msg = DB.join_party(pid, {'id': v[0], 'name': v[1]})
                    messagebox.showinfo('结果', msg)
                    dwin.destroy(); load_m()
                make_flat_button(dwin, '加入党派', pick, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12).pack(pady=8)
            make_flat_button(win, '➕ 添加党员', add_member, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12).pack(side='left', padx=10, pady=8)

            def kick():
                s = tree2.selection()
                if not s:
                    messagebox.showwarning('提示', '请选择一名党员'); return
                v = tree2.item(s[0])['values']
                DB.leave_party(pid, int(v[1]))
                messagebox.showinfo('成功', f'{v[2]} 已移出党派')
                load_m()
            make_flat_button(win, '移出党员', kick, COLOR_DANGER, COLOR_DANGER_HOVER, width=12).pack(side='left', padx=10, pady=8)

            def set_leader():
                s = tree2.selection()
                if not s:
                    messagebox.showwarning('提示', '请选择一名党员'); return
                v = tree2.item(s[0])['values']
                DB.set_party_leader(pid, int(v[1]), v[2])
                messagebox.showinfo('成功', f'{v[2]} 已成为党魁')
                win.destroy(); self._show_party_mgmt()
            make_flat_button(win, '设为党魁', set_leader, COLOR_WARNING, COLOR_WARNING, width=12).pack(side='left', padx=10, pady=8)

        tree.bind('<Double-1>', lambda e: view_members())
        btns = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btns.pack(pady=6)
        make_flat_button(btns, "👁️ 查看/管理党员", view_members, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=16).pack(side='left', padx=8)

        def dissolve():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning('提示', '请先选择一个党派'); return
            vals = tree.item(sel[0])['values']
            if not messagebox.askyesno('确认解散', f'确定解散党派《{vals[1]}》吗？'):
                return
            DB.delete_party(int(vals[0]))
            messagebox.showinfo('成功', '党派已解散')
            self._show_party_mgmt()
        make_flat_button(btns, "❌ 解散党派", dissolve, COLOR_DANGER, COLOR_DANGER_HOVER, width=12).pack(side='left', padx=8)
        make_flat_button(btns, "🔄 刷新", lambda: self._show_party_mgmt(), COLOR_GRAY, COLOR_GRAY, width=10).pack(side='left', padx=8)

    # ========== 选举中心（大选全流程 / 公民投票） ==========
    def _show_election_center(self):
        self._clear_main()
        role = self.user['type']
        make_page_title(self.main_frame, "🗳️ 总统大选中心（四年一届 · 10月23日开启 · 11月3日计票 · 次年1月20日就职）").pack(pady=10)
        tl = tk.Label(self.main_frame,
                      text="⏱ 换届制时间线：每4年 10月23日 系统自动开启大选（登记期间即可投票）→ 10月28日 截止参选登记 → "
                           "11月3日 自动结束计票 → 当选者于次年1月20日就职，连任者状态不变、未连任者转为总统卸任待定",
                      font=("Arial", 10, "bold"), bg=COLOR_PURPLE, fg='white', padx=10, pady=4)
        tl.pack(fill='x', padx=25, pady=(0, 4))

        # 大选列表
        list_f = tk.LabelFrame(self.main_frame, text="大选记录", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=10, pady=8)
        list_f.pack(fill='both', expand=True, padx=25, pady=8)
        cols = ('id', '标题', '状态', '发起人', '候选人', '已投票', '当选人', '就职日', '结束时间')
        tf, tree = make_tree(list_f, cols, {'id': 40, '标题': 110, '状态': 90, '发起人': 90,
                                            '候选人': 60, '已投票': 60, '当选人': 100, '就职日': 90,
                                            '结束时间': 130}, height=7)
        tf.pack(fill='both', expand=True)

        def load():
            for it in tree.get_children():
                tree.delete(it)
            for e in DB.list_elections():
                tree.insert('', 'end', values=(e['id'], e['title'], e['status'], e['started_by_name'],
                                               e['candidate_cnt'], e['vote_cnt'],
                                               e['winner_name'] or '-', e['inauguration_date'] or '-',
                                               e['ended_at'] or '-'))
        load()

        # 底部操作区
        op = tk.LabelFrame(self.main_frame, text="大选操作", font=("Arial", 12, "bold"),
                           bg=COLOR_CONTENT_BG, padx=12, pady=10)
        op.pack(fill='x', padx=25, pady=8)
        sel_id = {'v': None}
        def on_select(_=None):
            if tree.selection():
                sel_id['v'] = int(tree.item(tree.selection()[0])['values'][0])
        tree.bind('<<TreeviewSelect>>', on_select)

        # 推举候选人（党魁/admin 权力）
        if role in ('admin', 'official'):
            pf = tk.Frame(op, bg=COLOR_CONTENT_BG); pf.pack(fill='x', pady=4)
            tk.Label(pf, text="推举候选人：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left')
            cand_e = make_groove_entry(pf, width=14); cand_e.pack(side='left', padx=4)
            tk.Label(pf, text="（官员ID）", font=("Arial", 9), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(side='left')
            platform_e = make_groove_entry(pf, width=30); platform_e.pack(side='left', padx=4)
            tk.Label(pf, text="竞选纲领", font=("Arial", 9), bg=COLOR_CONTENT_BG, fg=COLOR_TEXT_GRAY).pack(side='left')
            pf2 = tk.Frame(op, bg=COLOR_CONTENT_BG); pf2.pack(fill='x', pady=2)
            tk.Label(pf2, text="副总统搭档身份证：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left')
            partner_idn_e = make_groove_entry(pf2, width=22); partner_idn_e.pack(side='left', padx=4)
            tk.Label(pf2, text="搭档姓名：", font=("Arial", 11), bg=COLOR_CONTENT_BG).pack(side='left')
            partner_name_e = make_groove_entry(pf2, width=12); partner_name_e.pack(side='left', padx=4)
            tk.Label(pf2, text="（户籍核对，就职日共同上任）", font=("Arial", 9), bg=COLOR_CONTENT_BG,
                     fg=COLOR_TEXT_GRAY).pack(side='left')

            def do_nominate():
                if not sel_id['v']:
                    messagebox.showwarning('提示', '请先选择一场大选'); return
                try:
                    oid = int(cand_e.get().strip())
                except ValueError:
                    messagebox.showwarning('提示', '请输入候选人官员ID'); return
                conn = DB.get_conn(GOV_DB)
                off = conn.execute("SELECT * FROM officials WHERE id=? AND status='在职'", (oid,)).fetchone()
                conn.close()
                if not off:
                    messagebox.showwarning('提示', '官员不存在或不在职'); return
                # 只能推举本党成员（党魁）或管理员
                party = DB.official_party(oid)
                if role == 'official':
                    if not party:
                        messagebox.showwarning('提示', '候选人必须加入党派后才能被推举'); return
                    my = DB.official_party(self.user['id'])
                    if not (my and my['id'] == party['id'] and my['leader_id'] == self.user['id']):
                        messagebox.showwarning('提示', '只有党魁可推举本党候选人'); return
                pname = party['name'] if party else '无党派'
                ok, msg, cid = DB.nominate_candidate(sel_id['v'], party['id'] if party else 0, pname, off,
                                                     platform_e.get().strip(),
                                                     partner_idn_e.get().strip(), partner_name_e.get().strip())
                if ok:
                    DB.log(role, self.user.get('id', ''), self.user['name'], '推举候选人',
                           f'推举{off["name"]}参加大选{sel_id["v"]}')
                    messagebox.showinfo('成功', msg)
                    self._show_election_center()
                else:
                    messagebox.showerror('失败', msg)
            make_flat_button(pf, '推举', do_nominate, COLOR_PURPLE, COLOR_PURPLE, width=8).pack(side='left', padx=6)

        bf = tk.Frame(op, bg=COLOR_CONTENT_BG); bf.pack(fill='x', pady=4)
        if role == 'admin':
            make_flat_button(bf, "▶️ 开始投票", lambda: self._election_act(sel_id, 'start', tree),
                             COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=12).pack(side='left', padx=6)
            make_flat_button(bf, "🏁 结束大选·计票", lambda: self._election_act(sel_id, 'close', tree),
                             COLOR_WARNING, COLOR_WARNING, width=16).pack(side='left', padx=6)
        # 公民/官员投票（投票中才可投）
        if role == 'citizen':
            make_flat_button(bf, "🗳️ 进入投票", lambda: self._citizen_vote(sel_id),
                             COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12).pack(side='left', padx=6)
        elif role == 'official':
            # 官员也是公民，可投票
            make_flat_button(bf, "🗳️ 官员投票", lambda: self._citizen_vote(sel_id, as_official=True),
                             COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=12).pack(side='left', padx=6)
        make_flat_button(bf, "👁️ 查看候选人/票数", lambda: self._view_candidates(sel_id),
                         COLOR_GRAY, COLOR_GRAY, width=16).pack(side='left', padx=6)
        make_flat_button(bf, "🗺️ 结果地图", lambda: self._show_election_map(sel_id),
                         COLOR_PRIMARY, COLOR_PRIMARY_HOVER, width=14).pack(side='left', padx=6)
        make_flat_button(bf, "🔄 刷新", lambda: self._show_election_center(), COLOR_GRAY, COLOR_GRAY, width=10).pack(side='left', padx=6)

    def _show_election_map(self, sel_id):
        """选举人团结果地图：6 州色块，按赢者染色；150 票进度条。"""
        if not sel_id['v']:
            messagebox.showwarning('提示', '请先选择一场大选'); return
        eid = sel_id['v']
        winner, breakdown, electoral, detail = DB.tally_electoral(eid)
        win_name = winner['official_name'] if winner else '待定'
        win_party = winner['party_name'] if winner else ''
        total_ev = sum(electoral.values()) or 280
        # 该大选所有候选人 → 颜色
        _cn = DB.get_conn(GOV_DB)
        try:
            cands = _cn.execute(
                "SELECT id, official_name, party_name FROM election_candidates WHERE election_id=?",
                (eid,)).fetchall()
        finally:
            _cn.close()
        palette = ['#e74c3c', '#3498db', '#27ae60', '#f39c12', '#9b59b6', '#1abc9c']
        cid_color = {r['id']: palette[i % len(palette)] for i, r in enumerate(cands)}
        cid_name = {r['id']: r['official_name'] for r in cands}
        # 弹窗
        win = tk.Toplevel(self.root)
        win.title(f"🗺️ 大选结果地图 #{eid}")
        win.geometry("820x560")
        win.configure(bg='white')
        tk.Label(win, text=f"选举人团结果 — {win_name}（{win_party}）",
                 font=("Arial", 18, "bold"), bg='white', fg=COLOR_TITLE).pack(pady=10)
        # 进度条
        prog_frame = tk.Frame(win, bg='white'); prog_frame.pack(pady=5)
        total_won = max(electoral.values()) if electoral else 0
        tk.Label(prog_frame, text=f"门槛 {ELECTORAL_MAJORITY} / 总 {sum(ELECTORAL_VOTES.values())}    当前最高 {total_won} 票",
                 bg='white', font=("Arial", 11)).pack()
        bar = tk.Frame(prog_frame, bg='#ecf0f1', width=500, height=20)
        bar.pack(pady=5)
        for cid, ev in sorted(electoral.items(), key=lambda x: -x[1]):
            seg = tk.Frame(bar, bg=cid_color.get(cid, '#bdc3c7'),
                           width=int(500 * ev / 280), height=20)
            seg.pack(side='left', fill='y')
            seg.pack_propagate(False)
        # 6 州色块（2 行 3 列）
        grid = tk.Frame(win, bg='white'); grid.pack(pady=15)
        for i, st in enumerate(ELECTORAL_STATES):
            sv = breakdown.get(st, {})
            if sv:
                top = max(sv, key=lambda x: sv[x])
                color = cid_color.get(top, '#bdc3c7')
                label = f"{st}\n{ELECTORAL_VOTES[st]}票\n→ {cid_name.get(top, top)}"
            else:
                color = '#bdc3c7'
                label = f"{st}\n{ELECTORAL_VOTES[st]}票\n无数据"
            cell = tk.Frame(grid, bg=color, width=220, height=130, bd=2, relief=tk.RAISED)
            cell.grid(row=i // 3, column=i % 3, padx=10, pady=10)
            cell.pack_propagate(False)
            tk.Label(cell, text=label, bg=color, fg='white',
                     font=("Arial", 12, "bold"), justify='center').pack(expand=True)
        # 明细
        detail_txt = '\n'.join(detail)
        tk.Label(win, text=detail_txt, bg='white', fg=COLOR_GRAY,
                 font=("Consolas", 9), justify='left').pack(pady=5)

    def _election_act(self, sel_id, act, tree):
        if not sel_id['v']:
            messagebox.showwarning('提示', '请先选择一场大选'); return
        eid = sel_id['v']
        if act == 'start':
            ok, msg = DB.start_voting(eid)
        else:
            ok, msg = DB.close_election(eid)
        if ok:
            DB.log(self.user['type'], self.user.get('id', ''), self.user['name'],
                   '大选操作', f'大选{eid} {act}')
            messagebox.showinfo('成功', msg)
            self._show_election_center()
        else:
            messagebox.showerror('失败', msg)

    def _view_candidates(self, sel_id):
        if not sel_id['v']:
            messagebox.showwarning('提示', '请先选择一场大选'); return
        eid = sel_id['v']
        cands = DB.election_candidates(eid)
        win = tk.Toplevel(self.root)
        win.title(f'大选 {eid} 候选人')
        win.geometry('680x420')
        win.transient(self.root); win.grab_set()
        tk.Label(win, text=f"大选 {eid} · 候选人名单与实时票数", font=("Arial", 12, "bold")).pack(pady=8)
        cols = ('id', '候选人', '所属党派', '副总统搭档', '部门', '职级', '竞选纲领', '得票')
        tf, tree = make_tree(win, cols, {'id': 40, '候选人': 90, '所属党派': 100, '副总统搭档': 110,
                                         '部门': 115, '职级': 115, '竞选纲领': 150, '得票': 60}, height=9)
        tf.pack(fill='both', expand=True, padx=12, pady=5)
        for cnd in cands:
            tree.insert('', 'end', values=(cnd['id'], cnd['official_name'], cnd['party_name'] or '-',
                                           cnd['partner_name'] or '-', cnd['department'] or '-',
                                           cnd['rank'] or '-', (cnd['platform'] or '')[:24], cnd['votes']))

    def _citizen_vote(self, sel_id, as_official=False):
        if not sel_id['v']:
            messagebox.showwarning('提示', '请先选择一场大选'); return
        eid = sel_id['v']
        e = next((x for x in DB.list_elections() if x['id'] == eid), None)
        if not e:
            messagebox.showerror('失败', '大选不存在'); return
        if e['status'] not in ('候选人登记', '投票中'):
            messagebox.showwarning('提示', '本次大选当前状态：' + e['status'] + '，仅候选人登记/投票阶段可投票')
            return
        idn = self.user.get('id_number', '')
        name = self.user['name']
        if as_official:
            conn = DB.get_conn(GOV_DB)
            off = conn.execute("SELECT id_number FROM officials WHERE id=?", (self.user['id'],)).fetchone()
            conn.close()
            idn = off['id_number'] if off else idn
        voted = DB.has_voted(eid, idn)
        if voted:
            messagebox.showinfo('提示', '您已投过票，每人只有一票')
            return
        cands = DB.election_candidates(eid)
        if not cands:
            messagebox.showwarning('提示', '暂无候选人'); return
        win = tk.Toplevel(self.root)
        win.title(f'总统大选投票 - {name}')
        win.geometry('620x440')
        win.transient(self.root); win.grab_set()
        tk.Label(win, text=f"{name}（{idn}）· 请选择您支持的总统候选人",
                 font=("Arial", 12, "bold")).pack(pady=10)
        cols = ('候选人ID', '候选人', '所属党派', '副总统搭档', '部门', '职级')
        tf, tree = make_tree(win, cols, {'候选人ID': 80, '候选人': 90, '所属党派': 110, '副总统搭档': 110,
                                         '部门': 130, '职级': 120}, height=8)
        tf.pack(fill='both', expand=True, padx=12)
        for cnd in cands:
            tree.insert('', 'end', values=(cnd['official_id'], cnd['official_name'],
                                           cnd['party_name'] or '-', cnd['partner_name'] or '-',
                                           cnd['department'] or '-', cnd['rank'] or '-'))
        def submit():
            s = tree.selection()
            if not s:
                messagebox.showwarning('提示', '请选择一位候选人'); return
            v = tree.item(s[0])['values']
            cand = next((c for c in cands if c['official_id'] == int(v[0])), None)
            if not cand:
                return
            if not messagebox.askyesno('确认投票', f'确认将您的一票投给 {v[1]} 吗？'):
                return
            ok, msg = DB.cast_vote(eid, idn, name, cand['id'])
            if ok:
                DB.log('citizen', idn, name, '大选投票', f'大选{eid} 投票给{v[1]}')
                messagebox.showinfo('成功', msg)
                win.destroy()
                self._show_election_center()
            else:
                messagebox.showerror('失败', msg)
        make_flat_button(win, '确认投票', submit, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=14).pack(pady=12)

    # ========== 管理员：修改密码 ==========
    def _show_change_pwd(self):
        self._clear_main()
        make_page_title(self.main_frame, "🔐 修改密码").pack(pady=20)

        form_f = tk.LabelFrame(self.main_frame, text="修改登录密码", font=("Arial", 12, "bold"),
                               bg=COLOR_CONTENT_BG, padx=30, pady=20)
        form_f.pack(pady=20, padx=120, fill=tk.X)

        tk.Label(form_f, text="原密码：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=0, column=0, padx=10, pady=12, sticky=tk.E)
        old_e = make_groove_entry(form_f, width=28, show='*')
        old_e.grid(row=0, column=1, padx=10, pady=12)

        tk.Label(form_f, text="新密码：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=1, column=0, padx=10, pady=12, sticky=tk.E)
        new_e = make_groove_entry(form_f, width=28, show='*')
        new_e.grid(row=1, column=1, padx=10, pady=12)

        tk.Label(form_f, text="确认新密码：", font=("Arial", 11), bg=COLOR_CONTENT_BG).grid(
            row=2, column=0, padx=10, pady=12, sticky=tk.E)
        new2_e = make_groove_entry(form_f, width=28, show='*')
        new2_e.grid(row=2, column=1, padx=10, pady=12)

        def do_change():
            old = old_e.get().strip()
            new = new_e.get().strip()
            new2 = new2_e.get().strip()
            if not old or not new or not new2:
                messagebox.showwarning('提示', '请填写完整')
                return
            if new != new2:
                messagebox.showwarning('提示', '两次输入的新密码不一致')
                return
            if len(new) < 6:
                messagebox.showwarning('提示', '新密码长度不能少于6位')
                return
            conn = DB.get_conn(GOV_DB)
            r = conn.execute("SELECT * FROM admins WHERE id=?", (self.user['id'],)).fetchone()
            if not r or r['password'] != old:
                conn.close()
                messagebox.showerror('错误', '原密码不正确')
                return
            conn.execute("UPDATE admins SET password=? WHERE id=?", (new, self.user['id']))
            conn.commit()
            conn.close()
            DB.log('admin', self.user['id'], self.user['name'], '修改密码', '管理员修改登录密码')
            messagebox.showinfo('成功', '密码修改成功，下次登录请使用新密码')
            old_e.delete(0, 'end')
            new_e.delete(0, 'end')
            new2_e.delete(0, 'end')

        btn_frame = tk.Frame(self.main_frame, bg=COLOR_CONTENT_BG)
        btn_frame.pack(pady=20)
        make_flat_button(btn_frame, "✅ 确认修改", do_change, COLOR_SUCCESS, COLOR_SUCCESS_HOVER, width=16).pack()

    # ========== 退出 ==========
    def _logout(self):
        if messagebox.askyesno('确认', '确定要退出登录吗？'):
            DB.log(self.user['type'], self.user.get('id', self.user.get('id_number', '')),
                   self.user['name'], '退出', '用户退出登录')
            self.root.destroy()

    def _on_close(self):
        self._logout()

    def run(self):
        self.root.mainloop()


# ========== 主入口 ==========
def _start_cert_service():
    """启动国籍证明扫码查验网页：本机端口 + cloudflared 公网隧道。
    公网隧道起来后二维码是 https 公网地址，手机连任何网络都能打开，查询走云端实时数据。"""
    try:
        import cert_service
        ok, port, err = cert_service.start(DB.cert_verify, land='袁泓涵政府')
        if not ok:
            print('[国籍证明查验] 本地服务启动失败：%s' % err)
            return False
        print('[国籍证明查验] 局域网地址：%s' % cert_service._lan_url())
        atexit.register(cert_service.stop_tunnel)  # 关程序时顺手关掉隧道，不留后台进程

        def _tunnel_worker():
            try:
                ok_t, url, err_t = cert_service.start_tunnel(port)
                if ok_t:
                    cert_service.save_public(url)
                    print('[国籍证明查验] 公网地址：%s' % url)
                else:
                    print('[国籍证明查验] 公网隧道未建立：%s' % err_t)
            except Exception as e:
                print('[国籍证明查验] 隧道异常：%s' % e)

        threading.Thread(target=_tunnel_worker, daemon=True).start()
        return True
    except Exception as e:
        print('[国籍证明查验] 启动异常：%s' % e)
        return False


def main():
    DB.init_gov_db()
    # 代码完整性自检：程序文件哈希必须与数据库基线一致（基线只会被系统更新流程刷新）
    try:
        DB.lock_program_file(True)   # 程序文件设为只读，普通编辑无法直接保存
    except Exception:
        pass
    try:
        _ok_int, _msg_int = DB.integrity_check()
        if not _ok_int:
            _r = tk.Tk()
            _r.withdraw()
            messagebox.showwarning(
                '完整性校验',
                _msg_int + '\n\n如果不是你自己改的代码，请通过「系统更新」流程发布与更新。')
            _r.destroy()
    except Exception:
        pass
    # 后台把户籍库里只有本地路径的人脸照片补传到云端（入籍申请人脸比对要用），不阻塞启动
    def _bg_sync_faces():
        try:
            ok, fail = DB.sync_faces_from_files()
            if ok:
                print('人脸照片已上云：%d 张，未找到本地文件 %d 张' % (ok, fail))
        except Exception:
            pass
    threading.Thread(target=_bg_sync_faces, daemon=True).start()
    _start_cert_service()
    while True:
        root = tk.Tk()
        login = LoginWindow(root)
        root.mainloop()
        if not login.current_user:
            break
        app = GovApp(login.current_user)
        app.run()


if __name__ == '__main__':
    main()
