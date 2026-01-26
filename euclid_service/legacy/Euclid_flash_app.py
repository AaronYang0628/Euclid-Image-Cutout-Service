#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Euclid Q1 Deep Learning Image Recognition + FITS Upload Service
"""

import os, io, re, time, base64, threading, json, shutil, logging
from pathlib import Path
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, List, Any
import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, render_template, url_for, send_file, send_from_directory
from flask_cors import CORS
from PIL import Image
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms, models
import matplotlib.pyplot as plt
from astropy.io import fits
from astropy.table import Table
from astropy.coordinates import SkyCoord
import astropy.units as u
import zipfile
# 导入euclid_cutout模块中的函数
from euclid_service.core.euclid_cutout_remix import process_catalog, query_tile_id
import uuid
from datetime import datetime

# ================== 日志配置 ==================
def setup_logging():
    """配置日志系统，同时输出到控制台和文件"""
    # 创建日志目录
    log_dir = Path.home() / "euclid_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成日志文件名（按日期）
    log_filename = log_dir / f"euclid_{datetime.now().strftime('%Y%m%d')}.log"
    
    # 创建根日志记录器
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # 清除现有的处理器
    logger.handlers.clear()
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    
    # 创建文件处理器
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    
    # 添加处理器到日志记录器
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    # 创建各个模块的日志记录器
    app_logger = logging.getLogger(__name__)
    app_logger.setLevel(logging.INFO)
    
    return app_logger

# 初始化日志系统
logger = setup_logging()

# ================== Configuration ==================
DATA_ROOT = Path("/media/aaron/DATA/astro+euclid/mirror/euclid_q1_102042")
CATALOG_DIR = DATA_ROOT / "catalogs"
IMAGE_DIRS = [DATA_ROOT / "VIS", DATA_ROOT / "NIR", DATA_ROOT / "MER"]

# Model and figure save directories
MODEL_DIR = Path.home() / "euclid_models"
FIG_DIR = MODEL_DIR / "figs"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
FIG_DIR.mkdir(parents=True, exist_ok=True)

# 目录配置
UPLOAD_DIR = '/home/aaron/tmp'  # 用户指定的上传目录
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'outputs')
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')
TMP_DIR = os.path.join(os.path.dirname(__file__), 'tmp')
# 新增持久化存储目录
PERMANENT_DOWNLOAD_DIR = '/home/aaron/tmp/Euclid_download/'  # 持久化下载目录
MAX_CATALOG_ROWS = 10000  # 最大星表行数

# 确保所有目录存在
for dir_path in [UPLOAD_DIR, OUTPUT_DIR, CACHE_DIR, TMP_DIR, PERMANENT_DOWNLOAD_DIR]:
    os.makedirs(dir_path, exist_ok=True)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

STATE = {"training": False, "message": "", "last_train_time": None, "model_path": None}

# 任务状态管理
MAX_CATALOG_ROWS = 10000
tasks = {}  # type: Dict[str, Dict]
tasks_lock = threading.Lock()

# Flask app initialization
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# 初始化时重置任务列表
def reset_tasks():
    """重置任务列表为空"""
    global tasks
    with tasks_lock:
        tasks = {}
        logger.info("任务列表已重置为空")

# 提供模板目录中的图片文件
@app.route('/templates/<path:filename>')
def serve_template_file(filename):
    """提供模板目录中的文件，用于访问图片等资源"""
    return send_from_directory(app.template_folder, filename)

# 导入裁剪功能
from euclid_service.core.euclid_cutout_remix import process_catalog

# 统一的文件命名和波段处理函数
def build_unified_filename(target_id, file_type, instrument, band, ra=None, dec=None):
    """构建统一格式的文件名：{target_id}_{instrument}_{file_type}_{band}.fits
    其中VIS波段可以省略band部分
    如果提供了RA和Dec且没有target_id，则使用RA_Dec命名格式
    
    Args:
        target_id: 目标ID
        file_type: 文件类型（如BGSUB, RMS, FLAG等）
        instrument: 仪器类型（如DECAM, VIS等）
        band: 波段信息（如VIS, NIR-Y, DES-G等）
        ra: 赤经坐标（可选）
        dec: 赤纬坐标（可选）
    
    Returns:
        str: 统一格式的文件名
    """
    # 标准化文件类型（去除连字符）
    file_type_clean = file_type.replace('-', '_')
    
    # 标准化仪器名称
    instrument_clean = instrument.upper()
    
    # 标准化波段名称
    band_clean = band.upper() if band else 'VIS'
    
    # 如果没有target_id但提供了RA和Dec，则使用RA_Dec命名格式
    if not target_id and ra is not None and dec is not None:
        # 格式化RA和Dec，保留足够的小数位并移除符号
        ra_str = f"{ra:.8f}".replace('.', '').replace('-', '')
        dec_str = f"{dec:.8f}".replace('.', '').replace('-', '')
        target_id = f"RA_{ra_str}_Dec_{dec_str}"
    elif not target_id:
        target_id = 'unknown'
    
    # VIS波段可以省略band部分，其他波段需要包含
    if band_clean == 'VIS':
        return f"{target_id}_{instrument_clean}_{file_type_clean}.fits"
    else:
        return f"{target_id}_{instrument_clean}_{file_type_clean}_{band_clean}.fits"

def extract_band_from_filename(filename):
    """从文件名中提取波段信息
    
    Args:
        filename: 文件名
    
    Returns:
        str: 波段信息，默认为VIS
    """
    # 支持的完整波段列表
    all_bands = ['VIS', 'NIR-Y', 'NIR-J', 'NIR-H', 'DES-G', 'DES-R', 'DES-I', 'DES-Z']
    
    # 基于用户提供的命名规范从文件名中提取波段
    name_parts = filename.replace('.fits', '').split('_')
    
    for part in name_parts:
        for band in all_bands:
            if band.lower() in part.lower():
                return band
    
    # 如果没有找到匹配的波段，返回VIS作为默认值
    return 'VIS'

def extract_band_from_file(file_path):
    """从文件路径中提取波段信息
    
    Args:
        file_path: 文件路径
    
    Returns:
        str: 波段信息，默认为VIS
    """
    # 支持的完整波段列表
    all_bands = ['VIS', 'NIR-Y', 'NIR-J', 'NIR-H', 'DES-G', 'DES-R', 'DES-I', 'DES-Z']
    
    # 首先尝试从文件名中提取
    filename = os.path.basename(file_path)
    band = extract_band_from_filename(filename)
    
    # 如果从文件名中找不到，尝试从FITS文件头中读取
    if band == 'VIS':
        try:
            with fits.open(file_path) as hdul:
                # 检查主HDU头
                if 'BAND' in hdul[0].header:
                    band = hdul[0].header['BAND']
                # 检查其他HDU头
                elif len(hdul) > 1:
                    for hdu in hdul[1:]:
                        if isinstance(hdu, fits.ImageHDU) and 'BAND' in hdu.header:
                            band = hdu.header['BAND']
                            break
        except Exception as e:
            logger.warning(f"从文件头读取波段信息失败: {e}")
    
    return band


def load_catalog(cat_path, ra_col=None, dec_col=None, id_col=None):
    """
    加载用于就近搜索的目录
    
    Args:
        cat_path: 目录文件路径
        ra_col: RA列名（可选）
        dec_col: DEC列名（可选）
        id_col: ID列名（可选）
    
    Returns:
        dict: 包含RA、DEC和ID的目录数据
    """
    from astropy.table import Table
    p = Path(cat_path)
    if not p.exists():
        raise FileNotFoundError(f"Catalog not found: {cat_path}")
    
    if p.suffix.lower() == '.csv' and pd is not None:
        df = pd.read_csv(p)
        cols = {c.lower(): c for c in df.columns}
        if ra_col is None:
            for cand in ['ra','right_ascension','ra_deg','rightascension']:
                if cand in cols:
                    ra_col = cols[cand]; break
        if dec_col is None:
            for cand in ['dec','declination','dec_deg']:
                if cand in cols:
                    dec_col = cols[cand]; break
        if id_col is None:
            for cand in ['object_id','source_id','id','targetid']:
                if cand in cols:
                    id_col = cols[cand]; break
        if ra_col is None or dec_col is None:
            raise ValueError("Could not find RA/DEC columns in CSV")
        ra = df[ra_col].astype(float).to_numpy()
        dec = df[dec_col].astype(float).to_numpy()
        ids = df[id_col].to_numpy() if id_col is not None else np.arange(len(ra))
    else:
        tab = Table.read(str(p))
        names = [n.lower() for n in tab.colnames]
        if ra_col is None:
            for cand in ['right_ascension','ra','ra_deg']:
                if cand in names:
                    ra_col = tab.colnames[names.index(cand)]; break
        if dec_col is None:
            for cand in ['declination','dec','dec_deg']:
                if cand in names:
                    dec_col = tab.colnames[names.index(cand)]; break
        if id_col is None:
            for cand in ['object_id','source_id','id','targetid']:
                if cand in names:
                    id_col = tab.colnames[names.index(cand)]; break
        if ra_col is None or dec_col is None:
            raise ValueError("Could not find RA/DEC columns in catalog")
        ra = np.array(tab[ra_col], dtype=float)
        dec = np.array(tab[dec_col], dtype=float)
        ids = np.array(tab[id_col]) if id_col is not None else np.arange(len(ra))
    return {'ra': ra, 'dec': dec, 'id': ids}


def find_nearest_object(ra, dec, catalog, max_sep_arcsec=1.0):
    """
    根据RA和Dec查找最近的object_id
    
    Args:
        ra: 目标RA坐标
        dec: 目标Dec坐标
        catalog: 用于搜索的目录数据
        max_sep_arcsec: 最大搜索距离（弧秒）
    
    Returns:
        tuple: (object_id, nearest_ra, nearest_dec, separation) 或 (None, None, None, None)
    """
    sky_cat = SkyCoord(catalog['ra']*u.deg, catalog['dec']*u.deg)
    sky_pt = SkyCoord(ra*u.deg, dec*u.deg)
    idx, sep2d, _ = sky_pt.match_to_catalog_sky(sky_cat)
    sep = sep2d.arcsec
    if sep <= max_sep_arcsec:
        return catalog['id'][idx], float(catalog['ra'][idx]), float(catalog['dec'][idx]), float(sep)
    else:
        return None, None, None, None

# 生成源的缓存键
def generate_source_cache_key(ra, dec, size, instrument, file_type, band=None):
    """为每个源生成唯一的缓存键，确保包含波段信息以区分不同波段的相同目标"""
    # 四舍五入到小数点后6位，避免浮点数精度问题
    ra_rounded = round(ra, 6)
    dec_rounded = round(dec, 6)
    # 确保缓存键总是包含波段信息，即使band为None也添加默认值
    # 这样可以确保不同波段的相同目标有不同的缓存键
    band_str = band if band else 'unknown'
    key_parts = [f"ra_{ra_rounded}", f"dec_{dec_rounded}", f"size_{size}", f"inst_{instrument}", f"type_{file_type}", f"band_{band_str}"]
    return "_".join(key_parts)

def scan_band_cache(target_ids, base_dir, instruments, file_types, bands=None):
    """扫描波段缓存目录中的所有fits文件，查找与目标ID、波段、仪器和文件类型匹配的文件
    重点在特定波段目录下(/data/home/xiejh/Euclid_download/波段/*.fits)搜索对应文件
    根据用户提供的命名规范精确识别文件类型"""
    cached_files = {}
    
    # 如果目录不存在，返回空字典
    if not os.path.exists(base_dir):
        return cached_files
    
    # 确保只选择一个仪器（按照需求，只支持单选）
    # 如果提供了多个仪器，只使用第一个
    if len(instruments) > 0:
        target_instrument = instruments[0]  # 只使用第一个仪器
    else:
        logger.error("未提供有效的仪器类型")
        return cached_files
    
    # 定义仪器与波段的映射关系
    band_map = {
        'NISP': ['NIR-Y', 'NIR-J', 'NIR-H'],
        'DECAM': ['DES-G', 'DES-R', 'DES-I', 'DES-Z'],
        'VIS': ['VIS'],
        'HSC': ['WISHES-G', 'WISHES-Z'],
        'GPC': ['PANSTARRS-I'],
        'MEGACAM': ['CFIS-U', 'CFIS-R']
    }
    
    # 使用用户选择的波段或根据仪器确定的默认波段
    if bands and len(bands) > 0:
        # 过滤出与所选仪器兼容的波段
        target_bands = [band for band in bands if band in band_map.get(target_instrument, [])]
    else:
        # 使用与所选仪器关联的所有波段
        target_bands = band_map.get(target_instrument, [])
    
    logger.info(f"开始扫描波段缓存目录，目标波段: {target_bands}")
    
    # 文件类型到命名模式的映射
    file_type_patterns = {
        'RMS': ['RMS', 'MOSAIC-.*-RMS'],
        'FLAG': ['FLAG', 'MOSAIC-.*-FLAG'],
        'BGMOD': ['BGMOD'],
        'BGSUB': ['BGSUB', 'BGSUB-MOSAIC'],
        'CATALOG-PSF': ['CATALOG-PSF']
    }
    
    # 遍历每个目标波段，在波段目录中搜索
    for band in target_bands:
        # 构建波段目录路径: /data/home/xiejh/Euclid_download/波段/
        target_dir = os.path.join(base_dir, band)
        
        # 如果目标目录不存在，尝试创建
        if not os.path.exists(target_dir):
            logger.warning(f"波段目录不存在: {target_dir}，尝试自动创建")
            try:
                os.makedirs(target_dir, exist_ok=True)
                logger.info(f"成功创建波段目录: {target_dir}")
            except Exception as e:
                logger.error(f"创建波段目录失败: {e}，跳过此波段")
                continue
        
        # 遍历波段目录中的所有文件
        for root, _, files in os.walk(target_dir):
            for file in files:
                if file.endswith('.fits'):
                    file_path = os.path.join(root, file)
                    
                    # 检查文件名中是否包含任何目标ID
                    matched_target_id = None
                    for target_id in target_ids:
                        if str(target_id) in file:
                            matched_target_id = str(target_id)  # 转换为字符串，确保可哈希
                            break
                    
                    # 如果没有匹配的目标ID，跳过这个文件
                    if not matched_target_id:
                        continue
                    
                    # 从文件名中提取文件类型
                    file_type = None
                    file_lower = file.lower()
                    
                    for ft in file_types:
                        # 检查文件名中是否包含文件类型标识
                        if ft.lower() in file_lower:
                            file_type = ft
                            break
                        
                        # 使用更具体的模式匹配
                        if ft in file_type_patterns:
                            for pattern in file_type_patterns[ft]:
                                if re.search(pattern, file, re.IGNORECASE):
                                    file_type = ft
                                    break
                        if file_type:
                            break
                    
                    # 只有当文件类型与用户选择匹配时，才添加到缓存
                    # 波段已经确定是当前遍历的band
                    if file_type in file_types:
                        if matched_target_id not in cached_files:
                            cached_files[matched_target_id] = []
                        cached_files[matched_target_id].append({
                            'path': file_path,
                            'band': band,
                            'instrument': target_instrument,
                            'file_type': file_type
                        })
                        logger.info(f"找到匹配波段缓存文件: {file} 对应TARGETID: {matched_target_id}, 波段: {band}, 仪器: {target_instrument}, 文件类型: {file_type}")
    
    logger.info(f"波段缓存扫描完成，找到 {sum(len(files) for files in cached_files.values())} 个匹配的缓存文件")
    return cached_files

def scan_permanent_cache(target_ids, base_dir, size, instruments, file_types, bands=None):
    """扫描持久化目录中的所有fits文件，查找与目标ID、波段、仪器、文件类型和尺寸匹配的文件
    重点在特定仪器目录下(MER/TILE_ID/仪器类型/*.fits)搜索对应文件
    根据用户提供的命名规范精确识别文件类型和波段"""
    cached_files = {}
    
    # 如果目录不存在，返回空字典
    if not os.path.exists(base_dir):
        return cached_files
    
    # 确保只选择一个仪器（按照需求，只支持单选）
    # 如果提供了多个仪器，只使用第一个
    if len(instruments) > 0:
        target_instrument = instruments[0]  # 只使用第一个仪器
    else:
        logger.error("未提供有效的仪器类型")
        return cached_files
    
    # 定义仪器与波段的映射关系
    band_map = {
        'NISP': ['NIR-Y', 'NIR-J', 'NIR-H'],
        'DECAM': ['DES-G', 'DES-R', 'DES-I', 'DES-Z'],
        'VIS': ['VIS'],
        'HSC': ['WISHES-G', 'WISHES-Z'],
        'GPC': ['PANSTARRS-I'],
        'MEGACAM': ['CFIS-U', 'CFIS-R']
    }
    
    # 使用用户选择的波段或根据仪器确定的默认波段
    if bands and len(bands) > 0:
        # 过滤出与所选仪器兼容的波段
        target_bands = [band for band in bands if band in band_map.get(target_instrument, [])]
    else:
        # 使用与所选仪器关联的所有波段
        target_bands = band_map.get(target_instrument, [])
    
    logger.info(f"开始扫描 {target_instrument} 仪器目录下的文件，目标波段: {target_bands}")
    
    # 文件类型到命名模式的映射
    file_type_patterns = {
        'RMS': ['RMS', 'MOSAIC-.*-RMS'],
        'FLAG': ['FLAG', 'MOSAIC-.*-FLAG'],
        'BGMOD': ['BGMOD'],
        'BGSUB': ['BGSUB', 'BGSUB-MOSAIC'],
        'CATALOG-PSF': ['CATALOG-PSF']
    }
    
    # 遍历每个目标ID，在特定的目录结构中搜索
    for target_id in target_ids:
        # 将target_id转换为字符串，确保它是可哈希的
        target_id_str = str(target_id)
        # 构建目标ID的目录路径: MER/TILE_ID/仪器类型/
        target_dir = os.path.join(base_dir, 'MER', f'TILE{target_id_str}', target_instrument)
        
        # 如果目标目录不存在，跳过
        if not os.path.exists(target_dir):
            logger.debug(f"目标目录不存在: {target_dir}")
            continue
        
        # 遍历目标目录中的所有文件
        for root, _, files in os.walk(target_dir):
            for file in files:
                if file.endswith('.fits'):
                    file_path = os.path.join(root, file)
                    
                    # 根据用户提供的命名规范提取波段信息
                    file_band = None
                    name_parts = file.split('_')
                    
                    # 检查文件名中是否包含目标ID
                    if target_id_str not in file:
                        continue
                    
                    # 从文件名中提取波段
                    for part in name_parts:
                        # 检查每个部分是否包含波段信息
                        for band in target_bands:
                            if band in part:
                                file_band = band
                                break
                        if file_band:
                            break
                    
                    # 从文件名中提取文件类型
                    file_type = None
                    file_lower = file.lower()
                    
                    for ft in file_types:
                        # 检查文件名中是否包含文件类型标识
                        if ft.lower() in file_lower:
                            file_type = ft
                            break
                        
                        # 使用更具体的模式匹配
                        if ft in file_type_patterns:
                            for pattern in file_type_patterns[ft]:
                                if re.search(pattern, file, re.IGNORECASE):
                                    file_type = ft
                                    break
                        if file_type:
                            break
                    
                    # 只有当文件的波段、仪器和文件类型与用户选择匹配时，才添加到缓存
                    if file_band in target_bands and file_type in file_types:
                        if target_id_str not in cached_files:
                            cached_files[target_id_str] = []
                        cached_files[target_id_str].append({
                            'path': file_path,
                            'band': file_band,
                            'instrument': target_instrument,
                            'file_type': file_type
                        })
                        logger.info(f"找到匹配缓存文件: {file} 对应TARGETID: {target_id}, 波段: {file_band}, 仪器: {target_instrument}, 文件类型: {file_type}")
    
    logger.info(f"扫描完成，找到 {sum(len(files) for files in cached_files.values())} 个匹配的缓存文件")
    return cached_files

# 处理任务函数
def process_task(task_id: str, catalog_path: str, config: Dict[str, Any]) -> None:
    """处理裁剪任务，带缓存功能和持久化存储，包含进度更新"""
    try:
        # 生成持久化存储目录路径
        permanent_task_dir = os.path.join(PERMANENT_DOWNLOAD_DIR, task_id)
        permanent_zip_path = os.path.join(permanent_task_dir, f"{task_id}.zip")
        
        # 更新任务状态为处理中
        with tasks_lock:
            tasks[task_id]['status'] = 'processing'
            tasks[task_id]['start_time'] = datetime.now().isoformat()
            tasks[task_id]['progress'] = 0  # 初始化进度
        
        # 检查是否已经有现成的处理结果
        if os.path.exists(permanent_zip_path) and os.path.getsize(permanent_zip_path) > 0:
            logger.info(f"找到已存在的处理结果，直接使用: {permanent_zip_path}")
            
            # 更新任务状态为完成，链接到已存在的结果
            with tasks_lock:
                tasks[task_id]['status'] = 'completed'
                tasks[task_id]['end_time'] = datetime.now().isoformat()
                tasks[task_id]['zip_path'] = permanent_zip_path
                tasks[task_id]['message'] = "使用缓存的处理结果"
                tasks[task_id]['progress'] = 100
                tasks[task_id]['stats'] = {
                    'total_sources': 0,
                    'cached_sources': 0,
                    'new_sources': 0,
                    'errors': 0,
                    'from_cache': True
                }
            return
        
        # 创建持久化存储目录
        os.makedirs(permanent_task_dir, exist_ok=True)
        
        # 读取星表
        catalog = Table.read(catalog_path)
        
        # 检查星表大小
        if len(catalog) > MAX_CATALOG_ROWS:
            catalog = catalog[:MAX_CATALOG_ROWS]
            with tasks_lock:
                tasks[task_id]['message'] = f"星表超过{MAX_CATALOG_ROWS}行，仅处理前{MAX_CATALOG_ROWS}行"
        
        # 记录处理开始时间，用于计算进度和速度
        start_processing_time = time.time()
        
        # 动态检测并使用合适的RA和DEC列名
        available_cols = catalog.colnames
        logger.info(f"星表可用列: {available_cols}")
        
        # 尝试查找TARGETID列
        target_id_col = None
        # 首先使用用户指定的列名
        user_target_id_col = config.get('target_id_col', 'object_id')
        if user_target_id_col in available_cols:
            target_id_col = user_target_id_col
        else:
            # 如果用户指定的列不存在，尝试常用别名
            target_id_aliases = ['TARGETID', 'ID', 'OBJECT_ID', 'SOURCE_ID']
            for alias in target_id_aliases:
                if alias in available_cols:
                    target_id_col = alias
                    break
        
        # 如果找到TARGETID列，收集所有TARGETID用于缓存检查
        target_ids = []
        if target_id_col:
            # 确保每个target_id都是字符串类型，避免MaskedConstant导致的哈希错误
            target_ids = []
            for source in catalog:
                raw_id = source[target_id_col]
                # 检查是否为MaskedConstant类型或其他特殊类型
                if hasattr(raw_id, 'mask'):  # MaskedConstant类型
                    continue  # 跳过掩码值
                else:
                    target_ids.append(str(raw_id))  # 转换为字符串
            logger.info(f"使用TARGETID列: {target_id_col}，找到 {len(target_ids)} 个目标ID")
        else:
            logger.warning("未找到TARGETID列，使用索引作为标识符")
        
        # 尝试查找合适的RA列
        ra_col = config['ra_col']
        if ra_col not in available_cols:
            # 尝试常用的RA列名变体
            ra_aliases = ['TARGET_RA', 'RA_1', 'RA_2', 'ra', 'Ra', 'RightAscension']
            found_ra = None
            for alias in ra_aliases:
                if alias in available_cols:
                    found_ra = alias
                    break
            
            if found_ra:
                logger.warning(f"未找到指定的RA列 '{ra_col}'，使用替代列 '{found_ra}'")
                ra_col = found_ra
            else:
                error_msg = f"星表中未找到合适的RA列，可用列名: {available_cols[:10]}"
                raise ValueError(error_msg)
        
        # 尝试查找合适的DEC列
        dec_col = config['dec_col']
        if dec_col not in available_cols:
            # 尝试常用的DEC列名变体
            dec_aliases = ['TARGET_DEC', 'DEC_1', 'DEC_2', 'dec', 'Dec', 'Declination']
            found_dec = None
            for alias in dec_aliases:
                if alias in available_cols:
                    found_dec = alias
                    break
            
            if found_dec:
                logger.warning(f"未找到指定的DEC列 '{dec_col}'，使用替代列 '{found_dec}'")
                dec_col = found_dec
            else:
                error_msg = f"星表中未找到合适的DEC列，可用列名: {available_cols[:10]}"
                raise ValueError(error_msg)
        
        logger.info(f"星表加载成功，包含 {len(catalog)} 行数据，使用列: RA={ra_col}, DEC={dec_col}")
        
        # 更新配置中的列名，供后续使用
        config['ra_col'] = ra_col
        config['dec_col'] = dec_col
        
        # 创建临时任务输出目录
        task_output_dir = os.path.join(TMP_DIR, task_id)
        os.makedirs(task_output_dir, exist_ok=True)
        
        for file_type in config["file_types"]:
            os.makedirs(os.path.join(task_output_dir, file_type), exist_ok=True)

        # 统计信息
        stats = {
            'total_sources': len(catalog),
            'cached_sources': 0,
            'new_sources': 0,
            'errors': 0,
            'permanent_cached': 0,
            'failed_targets': []  # 记录处理失败的目标及其原因
        }
        
        logger.info(f"开始处理任务 {task_id}，使用配置: {config}")
        
        # 准备缓存和处理的源
        sources_to_process = []
        cached_sources_info = []
        permanent_cached_files = []
        
        # 确保缓存目录存在
        for instrument in config['instruments']:
            for file_type in config['file_types']:
                os.makedirs(os.path.join(CACHE_DIR, instrument, file_type), exist_ok=True)
        
        # 从配置中获取必要参数
        size = config['size']
        
        # 扫描波段缓存目录中的缓存文件（使用TARGETID、波段、仪器和文件类型匹配）
        band_cache = {}
        if target_id_col:
            requested_bands = [config.get('band')] if config.get('band') else ['VIS']
            band_cache = scan_band_cache(
                target_ids=target_ids, 
                base_dir=PERMANENT_DOWNLOAD_DIR,
                instruments=config['instruments'],
                file_types=config['file_types'],
                bands=requested_bands
            )
            logger.info(f"在波段缓存目录中找到 {sum(len(files) for files in band_cache.values())} 个匹配的缓存文件")
        
        # 不再使用scan_permanent_cache函数，只使用band_cache来检索缓存文件
        
        # 从配置中获取必要参数
        ra_col = config['ra_col']
        dec_col = config['dec_col']
        size = config['size']
        
        for i, source in enumerate(catalog):
            ra = source[ra_col]
            dec = source[dec_col]
            # 确保target_id是可哈希类型，处理可能的MaskedConstant
            raw_target_id = source[target_id_col] if target_id_col else i
            if hasattr(raw_target_id, 'mask') and raw_target_id.mask:
                # 跳过掩码值
                logger.debug(f"跳过掩码的target_id: 行={i}")
                continue
            target_id = str(raw_target_id)
            
            # 记录已在波段缓存中找到的文件类型
            cached_file_types = set()
            has_band_cache = False
            
            # 检查波段缓存 - 只添加与用户请求的波段匹配的文件
            requested_band = config.get('band', 'VIS')
            if target_id in band_cache:
                for cached_info in band_cache[target_id]:
                    # 关键修复：只添加与用户请求波段匹配的文件
                    if cached_info['band'] == requested_band:
                        permanent_cached_files.append({
                            'cache_file': cached_info['path'],
                            'target_id': target_id,
                            'instrument': cached_info['instrument'],
                            'file_type': cached_info['file_type'],
                            'band': cached_info['band']
                        })
                        cached_file_types.add(cached_info['file_type'])
                        has_band_cache = True
                    else:
                        logger.debug(f"跳过不匹配的波段缓存: TARGETID={target_id}, 请求波段={requested_band}, 缓存波段={cached_info['band']}")
                
                # 只有当所有请求的文件类型和波段都在缓存中找到时，才增加统计计数
                # 检查是否有至少一个文件类型和波段匹配
                if cached_file_types and len([f for f in permanent_cached_files if f['target_id'] == target_id]) > 0:
                    matched_bands = set(f['band'] for f in permanent_cached_files if f['target_id'] == target_id)
                    
                    if requested_band in matched_bands and cached_file_types.issuperset(set(config['file_types'])):
                        stats['permanent_cached'] += 1
                        logger.info(f"找到所有匹配的文件类型和波段缓存: TARGETID={target_id}, 波段={matched_bands}")
                    else:
                        missing_types = set(config['file_types']) - cached_file_types
                        if requested_band not in matched_bands:
                            logger.info(f"找到部分匹配的波段缓存: TARGETID={target_id}, 缺失类型: {missing_types}, 缺失波段: {requested_band}")
                        else:
                            logger.info(f"找到部分匹配的波段缓存: TARGETID={target_id}, 缺失类型: {missing_types}")
                else:
                    logger.debug(f"波段缓存中没有匹配的文件: TARGETID={target_id}")
            
            # 处理部分匹配的波段缓存 - 为缺失波段创建处理任务
            if cached_file_types and len([f for f in permanent_cached_files if f['target_id'] == target_id]) > 0:
                matched_bands = set(f['band'] for f in permanent_cached_files if f['target_id'] == target_id)
                requested_band = config.get('band', 'VIS')
                
                if requested_band not in matched_bands:
                    # 为缺失的波段创建处理任务
                    logger.info(f"为TARGETID={target_id} 补充缺失波段: {requested_band}")
                    for file_type in config['file_types']:
                        # 检查该文件类型和波段的组合是否已处理
                        already_processed = any(
                            s['target_id'] == target_id and 
                            s['band'] == requested_band and 
                            s['file_type'] == file_type
                            for s in sources_to_process
                        )
                        if not already_processed:
                            sources_to_process.append({
                                'ra': ra,
                                'dec': dec,
                                'index': i,
                                'target_id': target_id,
                                'instrument': config['instruments'][0],
                                'file_type': file_type,
                                'band': requested_band
                            })
            
            # 检查本地缓存和处理新的源 - 不再只处理完全未缓存的文件类型
            # 即使文件类型在缓存中，如果尺寸或波段不同，也需要重新裁剪
            for instrument in config['instruments']:
                for file_type in config['file_types']:
                    # 适配单选波段模式
                    requested_band = config.get('band', 'VIS')
                    # 生成缓存键
                    cache_key = generate_source_cache_key(ra, dec, size, instrument, file_type, requested_band)
                    cache_file = os.path.join(CACHE_DIR, instrument, file_type, f"{cache_key}.fits")
                    
                    # 检查缓存是否存在
                    if os.path.exists(cache_file):
                        # 记录缓存信息，稍后复制到输出目录和持久化目录
                        cached_sources_info.append({
                            'cache_file': cache_file,
                            'instrument': instrument,
                            'file_type': file_type,
                            'source_index': i,
                            'target_id': target_id,
                            'band': requested_band
                        })
                        stats['cached_sources'] += 1
                    else:
                        # 添加到需要处理的源列表 - 这包括全新组合或补充波段
                        sources_to_process.append({
                            'ra': ra,
                            'dec': dec,
                            'index': i,
                            'target_id': target_id,
                            'instrument': instrument,
                            'file_type': file_type,
                            'band': requested_band
                        })
            
            # 每处理10个源更新一次进度
            if i % 10 == 0 or i == len(catalog) - 1:
                progress = int((i + 1) / len(catalog) * 100)
                with tasks_lock:
                    tasks[task_id]['progress'] = progress
                    tasks[task_id]['message'] = f"正在检查缓存: {i+1}/{len(catalog)}"
        
        # 处理未缓存的源
        if sources_to_process:
            logger.info(f"处理 {len(sources_to_process)} 个新源...")
            
            # 创建临时处理目录
            temp_process_dir = os.path.join(TMP_DIR, f"temp_process_{task_id}")
            os.makedirs(temp_process_dir, exist_ok=True)

            for file_type in config["file_types"]:
                os.makedirs(os.path.join(temp_process_dir, file_type), exist_ok=True)
            
            # 创建临时星表，只包含需要处理的源
            processed_indices = list(set([s['index'] for s in sources_to_process]))
            processed_catalog = catalog[processed_indices]
            
            # 执行裁剪 - 使用euclid_cutout.py中的process_catalog函数
            # 支持的文件类型: BGSUB, CATALOG-PSF, FLAG, BGMOD, RMS, GRID-PSF
            # 支持的仪器: VIS, NISP, DECAM
            # 支持的波段根据仪器不同: NISP(NIR-Y, NIR-J, NIR-H), DECAM(DES-G, DES-R, DES-I, DES-Z), VIS(VIS)
            process_stats = process_catalog(
                catalog=processed_catalog,
                output_dir=temp_process_dir,
                file_types=config['file_types'],
                ra_col=config['ra_col'],
                dec_col=config['dec_col'],
                size=config['size'],
                obj_id_col=target_id_col,  # 传递TARGETID列名，可能为None
                mer_root= DATA_ROOT / 'MER',  # 确保路径正确
                instruments=config['instruments'],
                bands=[config.get('band', 'VIS')],  # 适配单选模式
                skip_nan=True,
                save_catalog_row=True,
                parallel=True,
                n_workers=config['n_workers'],
                verbose=False
            )
            
            # 更新统计信息
            stats['new_sources'] = process_stats.get('success', 0)
            stats['errors'] = process_stats.get('error', 0)
            
            # 复制新生成的文件到缓存、任务输出目录和持久化目录
            processed_count = 0
            # 确保所有需要的目录存在
            os.makedirs(task_output_dir, exist_ok=True)
            os.makedirs(permanent_task_dir, exist_ok=True)
            
            # 为每个目标单独创建文件，确保每个源都有对应的文件
            for i, source in enumerate(catalog):
                ra = source[ra_col]
                dec = source[dec_col]
                target_id = source[target_id_col] if target_id_col else None
                
                # 确保输出目录存在
                os.makedirs(task_output_dir, exist_ok=True)
                os.makedirs(permanent_task_dir, exist_ok=True)
                
                # 标志变量：记录是否有文件被成功处理
                files_processed_for_target = False
                
                # 处理process_catalog生成的文件结构（按文件类型分类）
                for file_type in config['file_types']:
                    # 查找该文件类型目录下的对应文件
                    file_type_dir = os.path.join(temp_process_dir, file_type)
                    if os.path.exists(file_type_dir):
                        # 获取目录中的所有FITS文件
                        all_fits_files = [f for f in os.listdir(file_type_dir) if f.endswith('.fits')]
                        if not all_fits_files:
                            logger.debug(f"{file_type_dir} 中没有FITS文件")
                            continue
                        
                        # 查找匹配的文件 - 根据target_id或RA/Dec命名
                        matching_files = []
                        
                        # 如果有target_id，使用它来匹配文件
                        if target_id is not None:
                            target_id_str = str(target_id)
                            # 检查目录中是否有包含target_id_str的文件
                            for file in all_fits_files:
                                if target_id_str in file:
                                    matching_files.append(file)
                        
                        # 如果没有target_id或没有找到匹配的文件，使用RA/Dec命名来匹配
                        if not matching_files:
                            # 使用RA/Dec生成文件名的模式
                            ra_pattern = f"ra_{ra:.6f}"
                            dec_pattern = f"dec_{dec:.6f}"
                            # 检查目录中是否有包含RA和Dec模式的文件
                            for file in all_fits_files:
                                if ra_pattern in file and dec_pattern in file:
                                    matching_files.append(file)
                        
                        # 如果没有找到匹配的文件，跳过这个源
                        if not matching_files:
                            logger.debug(f"TARGETID {target_id} 在 {file_type_dir} 中没有找到匹配的FITS文件")
                            continue
                        
                        for match_file in matching_files:
                            try:
                                source_file_path = os.path.join(file_type_dir, match_file)
                                
                                # 检查文件是否存在且大小大于0
                                if not os.path.exists(source_file_path) or os.path.getsize(source_file_path) == 0:
                                    logger.warning(f"TARGETID {target_id}: 文件不存在或为空: {source_file_path}")
                                    continue
                                
                                # 根据文件名格式提取波段信息，基于用户提供的命名规范
                                file_band = None
                                
                                # 支持的完整波段列表
                                all_bands = ['VIS', 'NIR-Y', 'NIR-J', 'NIR-H', 'DES-G', 'DES-R', 'DES-I', 'DES-Z']
                                
                                # 基于用户提供的命名规范，从文件名中提取波段
                                # 格式示例：EUC_MER_MOSAIC-VIS-RMS_TILE102019587-688F6A_20241018T164415.012922Z_00.00.fits
                                # EUC_MER_MOSAIC-NIR-Y-RMS_TILE102019587-DEDE9D_20241018T160516.574568Z_00.00.fits
                                # EUC_MER_MOSAIC-DES-R-RMS_TILE102019587-FB06DB_20241018T160347.414431Z_00.00.fits
                                # EUC_MER_CATALOG-PSF-VIS_TILE102019587-F8109_2019T013823.091936Z_00.00.fits
                                
                                # 分割文件名以找到波段标识
                                name_parts = match_file.split('_')
                                for part in name_parts:
                                    # 检查是否包含波段标识
                                    for band in all_bands:
                                        if band in part:
                                            file_band = band
                                            break
                                    if file_band:
                                        break
                                
                                # 如果基于命名规范没找到，回退到之前的方法
                                if not file_band:
                                    # 优先检查配置中的波段
                                    for band in config.get('bands', all_bands):
                                        if band in match_file:
                                            file_band = band
                                            break
                                    
                                    # 如果通过配置中的波段没找到，尝试所有支持的波段
                                    if not file_band:
                                        for band in all_bands:
                                            if band in match_file:
                                                file_band = band
                                                break
                                    
                                    # 如果仍然找不到波段信息，尝试从fits文件头中读取
                                    if not file_band:
                                        try:
                                            with fits.open(source_file_path) as hdul:
                                                # 检查主HDU头
                                                if 'BAND' in hdul[0].header:
                                                    file_band = hdul[0].header['BAND']
                                                # 检查其他HDU头
                                                elif len(hdul) > 1:
                                                    for hdu in hdul[1:]:
                                                        if isinstance(hdu, fits.ImageHDU) and 'BAND' in hdu.header:
                                                            file_band = hdu.header['BAND']
                                                            break
                                        except Exception as e:
                                            logger.debug(f"读取FITS头失败: {e}")
                                
                                # 如果所有方法都找不到波段信息，使用默认波段
                                if not file_band:
                                    file_band = 'VIS'  # 默认波段
                                
                                # 构建输出文件名 - 优先使用object_id命名，如果没有文件产生则使用RA/Dec命名
                                output_filename = build_unified_filename(target_id, file_type, config['instruments'][0], file_band, ra=ra, dec=dec)
                                  
                                logger.debug(f"为文件 {match_file} 识别的波段: {file_band}")
                                  
                                # 复制到持久化目录（按波段分类）
                                band_permanent_dir = os.path.join(permanent_task_dir, file_band)
                                os.makedirs(band_permanent_dir, exist_ok=True)
                                permanent_output_file = os.path.join(band_permanent_dir, output_filename)
                                shutil.copy2(source_file_path, permanent_output_file)
                                  
                                # 复制到任务输出目录（按波段分类）
                                band_task_dir = os.path.join(task_output_dir, file_band)
                                os.makedirs(band_task_dir, exist_ok=True)
                                task_output_file = os.path.join(band_task_dir, output_filename)
                                shutil.copy2(permanent_output_file, task_output_file)
                                  
                                # 备份到波段缓存目录 - 只有当文件不存在时才备份
                                band_cache_dir = os.path.join(PERMANENT_DOWNLOAD_DIR, file_band)
                                os.makedirs(band_cache_dir, exist_ok=True)
                                band_cache_file = os.path.join(band_cache_dir, output_filename)
                                if not os.path.exists(band_cache_file):
                                    try:
                                        shutil.copy2(source_file_path, band_cache_file)
                                        logger.info(f"备份文件到波段缓存目录: {band_cache_file}")
                                    except Exception as e:
                                        logger.error(f"备份到波段缓存目录失败: {e}")
                                else:
                                    logger.debug(f"波段缓存目录中已存在文件: {band_cache_file}，跳过备份")
                                  
                                # 记录保存信息
                                if target_id:
                                    logger.info(f"已保存TARGETID {target_id}: {output_filename} ({file_type}) 到 {permanent_task_dir}")
                                else:
                                    logger.info(f"已保存RA={ra}, DEC={dec}: {output_filename} ({file_type}) 到 {permanent_task_dir}")
                                files_processed_for_target = True
                            except Exception as e:
                                logger.error(f"复制文件 {match_file} 时出错: {e}")
                                stats['errors'] += 1
                    
                    # 检查是否有文件被处理
                    if not files_processed_for_target:
                        # 分析可能的原因
                        error_reasons = []
                         
                        # 检查临时处理目录是否存在
                        if not os.path.exists(temp_process_dir):
                            error_reasons.append(f"临时处理目录不存在: {temp_process_dir}")
                         
                        # 检查是否有任何文件类型目录
                        for file_type in config['file_types']:
                            file_type_dir = os.path.join(temp_process_dir, file_type)
                            if not os.path.exists(file_type_dir):
                                error_reasons.append(f"文件类型目录不存在: {file_type_dir}")
                            else:
                                    # 检查目录中是否有任何FITS文件
                                    all_files = [f for f in os.listdir(file_type_dir) if f.endswith('.fits')]
                                    if not all_files:
                                        error_reasons.append(f"{file_type_dir} 中没有FITS文件")
                                    else:
                                        # 临时处理目录中的文件是裁剪后的结果，不包含原始target_id
                                        # 错误信息应该更加通用，说明没有找到与当前目标相关的处理结果
                                        if target_id:
                                            error_reasons.append(f"{file_type_dir} 中有FITS文件，但没有找到与TARGETID {target_id} (RA={ra}, DEC={dec}) 相关的处理结果")
                                        else:
                                            error_reasons.append(f"{file_type_dir} 中有FITS文件，但没有找到与RA={ra}, DEC={dec} 相关的处理结果")
                        
                        # 直接跳过object_id验证，使用RA/Dec保存裁剪结果
                        nearest_file_processed = False
                        
                        # 如果直接跳过object_id验证，记录状态
                        if not nearest_file_processed:
                            # 输出错误信息
                            # 根据是否有target_id生成不同的错误信息
                            if target_id:
                                logger.error(f"TARGETID {target_id} 没有文件被处理成功！")
                            else:
                                logger.error(f"RA={ra}, DEC={dec} 没有文件被处理成功！")
                            logger.error(f"可能的原因: {'; '.join(error_reasons)}")
                            logger.error(f"检查点: RA={ra}, DEC={dec}, SIZE={size}, 临时目录={temp_process_dir}")
                            stats['errors'] += 1
                            stats['failed_targets'].append({
                                'target_id': target_id,
                                'ra': ra,
                                'dec': dec,
                                'size': size,
                                'reasons': error_reasons
                            })
                        else:
                            # 根据是否有target_id生成不同的日志信息
                            if target_id:
                                logger.info(f"TARGETID {target_id} 通过就近object_id {nearest_id} 处理完成")
                            else:
                                logger.info(f"RA={ra}, DEC={dec} 通过就近object_id {nearest_id} 处理完成")
                            files_processed_for_target = True
                    else:
                        # 记录成功日志
                        if target_id:
                            logger.info(f"TARGETID {target_id} 处理完成")
                        else:
                            logger.info(f"RA={ra}, DEC={dec} 处理完成")
                    
                    # 更新进度
                    processed_count += 1
                    progress = int(50 + (processed_count / len(catalog) * 50))
                    success_count = processed_count  # 假设全部成功，实际应从处理结果获取
                    failure_count = 0
                    
                    # 计算预计剩余时间（简单估算）
                    current_time = time.time()
                    elapsed = current_time - start_processing_time
                    avg_time_per_item = elapsed / processed_count
                    remaining = avg_time_per_item * (len(catalog) - processed_count)
                    remaining_str = time.strftime('%M:%S', time.gmtime(remaining))
                    
                    # 生成进度条
                    bar_length = 30
                    filled_length = int(bar_length * progress / 100)
                    bar = '█' * filled_length + '▋' if progress % 10 >= 5 and filled_length < bar_length else '█' * filled_length
                    bar = bar.ljust(bar_length)
                    
                    # 更新任务状态，包含详细进度信息
                    with tasks_lock:
                        tasks[task_id]['progress'] = min(progress, 99)
                        tasks[task_id]['message'] = f"批量裁剪: {progress}%|{bar}| {processed_count}/{len(catalog)} [{time.strftime('%M:%S', time.gmtime(elapsed))}<{remaining_str}, {processed_count/elapsed:.2f}it/s, 成功={success_count}, 失败={failure_count}]"
                        # 存储额外的进度信息
                        tasks[task_id]['processing_details'] = {
                            'processed': processed_count,
                            'total': len(catalog),
                            'success': success_count,
                            'failed': failure_count,
                            'speed': f"{processed_count/elapsed:.2f}it/s",
                            'remaining': remaining_str
                        }
            
            # 清理临时处理目录
            shutil.rmtree(temp_process_dir, ignore_errors=True)
        
        # 复制本地缓存的文件到任务输出目录和持久化目录
        for cache_info in cached_sources_info:
            try:
                # 构建输出文件名 - 使用TARGETID而不是索引
                instrument = cache_info['instrument']
                file_type = cache_info['file_type']
                target_id = cache_info['target_id']
                band = cache_info.get('band')
                
                # 从缓存信息中提取波段，或者尝试从文件名中识别
                band_from_cache = cache_info.get('band')
                file_band = band_from_cache
                
                # 如果缓存中没有波段信息，尝试从文件名中识别
                if not file_band:
                    # 支持的完整波段列表
                    all_bands = ['VIS', 'NIR-Y', 'NIR-J', 'NIR-H', 'DES-G', 'DES-R', 'DES-I', 'DES-Z']
                    
                    # 基于用户提供的命名规范从文件名中提取波段
                    cache_filename = os.path.basename(cache_info['cache_file'])
                    name_parts = cache_filename.split('_')
                    
                    # 首先检查文件名中的波段标识
                    for part in name_parts:
                        for band in all_bands:
                            if band in part:
                                file_band = band
                                break
                        if file_band:
                            break
                    
                    # 如果基于命名规范没找到，检查文件名中是否直接包含波段
                    if not file_band:
                        for band in all_bands:
                            if band in cache_filename:
                                file_band = band
                                break
                    
                    # 如果仍然找不到，尝试从文件内容读取
                    if not file_band:
                        try:
                            with fits.open(cache_info['cache_file']) as hdul:
                                if 'BAND' in hdul[0].header:
                                    file_band = hdul[0].header['BAND']
                                elif len(hdul) > 1:
                                    for hdu in hdul[1:]:
                                        if isinstance(hdu, fits.ImageHDU) and 'BAND' in hdu.header:
                                            file_band = hdu.header['BAND']
                                            break
                        except:
                            pass
                
                # 最终默认波段
                if not file_band:
                    file_band = 'VIS'  # 默认波段
                
                # 构建输出文件名，使用正确的波段信息确保文件名唯一
                if target_id_col:
                    # 使用统一的命名格式
                    output_filename = build_unified_filename(target_id, file_type, instrument, file_band)
                else:
                    source_index = cache_info['source_index']
                    # 使用统一的命名格式
                    source_target_id = f"source{source_index}"
                    output_filename = build_unified_filename(source_target_id, file_type, instrument, file_band)
                
                # 确保输出目录存在
                os.makedirs(task_output_dir, exist_ok=True)
                os.makedirs(permanent_task_dir, exist_ok=True)
                    
                    # 如果仍然找不到，尝试从文件内容读取
                if not file_band:
                        try:
                            with fits.open(cache_info['cache_file']) as hdul:
                                if 'BAND' in hdul[0].header:
                                    file_band = hdul[0].header['BAND']
                                elif len(hdul) > 1:
                                    for hdu in hdul[1:]:
                                        if isinstance(hdu, fits.ImageHDU) and 'BAND' in hdu.header:
                                            file_band = hdu.header['BAND']
                                            break
                        except:
                            pass
                
                # 最终默认波段
                if not file_band:
                    file_band = 'VIS'  # 默认波段
                
                logger.debug(f"缓存文件处理: {cache_filename} 识别的波段: {file_band}")
                
                # 复制到持久化目录（按波段分类）
                band_permanent_dir = os.path.join(permanent_task_dir, file_band)
                os.makedirs(band_permanent_dir, exist_ok=True)
                permanent_output_file = os.path.join(band_permanent_dir, output_filename)
                shutil.copy2(cache_info['cache_file'], permanent_output_file)
                
                # 复制到任务输出目录（按波段分类）
                band_task_dir = os.path.join(task_output_dir, file_band)
                os.makedirs(band_task_dir, exist_ok=True)
                task_output_file = os.path.join(band_task_dir, output_filename)
                shutil.copy2(permanent_output_file, task_output_file)
                
                # 备份到波段缓存目录 - 只有当文件不存在时才备份
                band_cache_dir = os.path.join(PERMANENT_DOWNLOAD_DIR, file_band)
                os.makedirs(band_cache_dir, exist_ok=True)
                band_cache_file = os.path.join(band_cache_dir, output_filename)
                if not os.path.exists(band_cache_file):
                    try:
                        shutil.copy2(cache_info['cache_file'], band_cache_file)
                        logger.info(f"备份文件到波段缓存目录: {band_cache_file}")
                    except Exception as e:
                        logger.error(f"备份到波段缓存目录失败: {e}")
                else:
                    logger.debug(f"波段缓存目录中已存在文件: {band_cache_file}，跳过备份")
                
                logger.info(f"从本地缓存复制: {output_filename} 到 {permanent_task_dir}")
                
            except Exception as e:
                logger.error(f"复制缓存文件 {cache_info['cache_file']} 时出错: {e}")
                stats['errors'] += 1
        
        # 复制持久化缓存中的文件到任务输出目录
        for cache_info in permanent_cached_files:
            try:
                # 使用原始文件名
                original_filename = os.path.basename(cache_info['cache_file'])
                
                # 确保输出目录存在
                os.makedirs(task_output_dir, exist_ok=True)
                
                # 根据文件名格式提取波段信息，基于用户提供的命名规范
                file_band = None
                
                # 支持的完整波段列表
                all_bands = ['VIS', 'NIR-Y', 'NIR-J', 'NIR-H', 'DES-G', 'DES-R', 'DES-I', 'DES-Z']
                
                # 首先检查缓存信息中是否已经包含波段
                if 'band' in cache_info and cache_info['band']:
                    file_band = cache_info['band']
                else:
                    # 基于用户提供的命名规范从文件名中提取波段
                    name_parts = original_filename.split('_')
                    for part in name_parts:
                        for band in all_bands:
                            if band in part:
                                file_band = band
                                break
                        if file_band:
                            break
                    
                    # 如果基于命名规范没找到，回退到检查文件名中是否包含波段
                    if not file_band:
                        for band in config.get('bands', all_bands):
                            if band in original_filename:
                                file_band = band
                                break
                
                if not file_band:
                    file_band = 'VIS'  # 默认波段
                
                # 生成包含波段信息的文件名，避免文件覆盖
                # 首先尝试从原始文件名中提取基础部分（去除波段信息）
                filename_parts = original_filename.replace('.fits', '').split('_')
                base_filename_parts = []
                has_band_in_name = False
                
                for part in filename_parts:
                    if part in all_bands:
                        has_band_in_name = True
                        continue  # 跳过波段部分，我们会在后面添加
                    base_filename_parts.append(part)
                
                # 如果文件名中已经包含波段信息，保留原始文件名
                # 如果没有包含，则根据波段重命名避免覆盖
                if has_band_in_name or file_band == 'VIS':
                    filename = original_filename
                else:
                    # 重新构建包含波段信息的文件名
                    base_name = '_'.join(base_filename_parts)
                    filename = f"{base_name}_{file_band}.fits"
                
                # 也复制到当前任务的持久化目录（按波段分类）
                band_permanent_dir = os.path.join(permanent_task_dir, file_band)
                permanent_output_file = os.path.join(band_permanent_dir, filename)
                os.makedirs(band_permanent_dir, exist_ok=True)
                shutil.copy2(cache_info['cache_file'], permanent_output_file)
                
                # 复制到任务输出目录（按波段分类）
                band_task_dir = os.path.join(task_output_dir, file_band)
                task_output_file = os.path.join(band_task_dir, filename)
                os.makedirs(band_task_dir, exist_ok=True)
                shutil.copy2(permanent_output_file, task_output_file)
                
                # 备份到波段缓存目录 - 只有当文件不存在时才备份
                band_cache_dir = os.path.join(PERMANENT_DOWNLOAD_DIR, file_band)
                os.makedirs(band_cache_dir, exist_ok=True)
                band_cache_file = os.path.join(band_cache_dir, filename)
                if not os.path.exists(band_cache_file):
                    try:
                        shutil.copy2(cache_info['cache_file'], band_cache_file)
                        logger.info(f"备份文件到波段缓存目录: {band_cache_file}")
                    except Exception as e:
                        logger.error(f"备份到波段缓存目录失败: {e}")
                else:
                    logger.debug(f"波段缓存目录中已存在文件: {band_cache_file}，跳过备份")
                
                logger.info(f"从持久化缓存复制: {filename} 到任务输出目录和当前持久化目录")
                
            except Exception as e:
                logger.error(f"复制持久化缓存文件 {cache_info['cache_file']} 时出错: {e}")
                stats['errors'] += 1
        
        # 更新进度为99%，准备打包
        with tasks_lock:
            tasks[task_id]['progress'] = 99
            tasks[task_id]['message'] = f"批量裁剪: 99%|{'█' * 30}| {len(catalog)}/{len(catalog)} [处理完成，正在打包结果...]"
        
        # 创建结果ZIP文件 - 同时保存在临时目录和持久化目录
        zip_path = os.path.join(OUTPUT_DIR, f"{task_id}.zip")
        permanent_zip_path = os.path.join(permanent_task_dir, f"{task_id}.zip")
        
        # 确保输出目录存在
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(permanent_task_dir, exist_ok=True)
        
        # 收集任务输出目录中的所有FITS文件
        task_fits_files = []
        for root, _, files in os.walk(task_output_dir):
            for file in files:
                if file.endswith('.fits'):
                    file_path = os.path.join(root, file)
                    task_fits_files.append((file_path, file))
        
        # 收集持久化目录中的所有FITS文件
        permanent_fits_files = []
        for root, _, files in os.walk(permanent_task_dir):
            for file in files:
                if file.endswith('.fits') and file != f"{task_id}.zip":
                    file_path = os.path.join(root, file)
                    permanent_fits_files.append((file_path, file))
        
        logger.info(f"任务目录中有 {len(task_fits_files)} 个FITS文件")
        logger.info(f"持久化目录中有 {len(permanent_fits_files)} 个FITS文件")
        
        # 先创建持久化版本的ZIP文件
        with zipfile.ZipFile(permanent_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 优先添加持久化目录中的文件（保持目录结构）
            for file_path, file in permanent_fits_files:
                try:
                    # 计算相对路径，保持波段目录结构
                    rel_path = os.path.relpath(file_path, permanent_task_dir)
                    zipf.write(file_path, rel_path)
                    logger.debug(f"添加到ZIP: {rel_path}")
                except Exception as e:
                    logger.error(f"添加文件 {file} 到ZIP时出错: {e}")
            
            # 如果持久化目录中没有文件，添加任务目录中的文件（保持目录结构）
            if len(permanent_fits_files) == 0:
                for file_path, file in task_fits_files:
                    try:
                        # 计算相对路径，保持波段目录结构
                        rel_path = os.path.relpath(file_path, task_output_dir)
                        zipf.write(file_path, rel_path)
                        logger.debug(f"从任务目录添加到ZIP: {rel_path}")
                    except Exception as e:
                        logger.error(f"添加文件 {file} 到ZIP时出错: {e}")
        
        # 验证ZIP文件是否创建成功且不为空
        if os.path.exists(permanent_zip_path):
            zip_size = os.path.getsize(permanent_zip_path)
            logger.info(f"持久化结果已保存到: {permanent_zip_path} (大小: {zip_size/1024:.2f} KB)")
            
            # 如果ZIP文件为空，添加一个简单的README文件
            if zip_size == 0:
                logger.warning("警告: ZIP文件为空，创建README文件并重新打包")
                with open(os.path.join(temp_process_dir, "README.txt"), "w") as f:
                    f.write(f"Euclid Q1 裁剪结果 - 任务ID: {task_id}\n")
                    f.write(f"处理时间: {datetime.now().isoformat()}\n")
                    f.write(f"星表包含 {len(catalog)} 个源\n")
                    f.write("注意: 没有生成有效的FITS文件\n")
                
                # 重新创建ZIP文件
                with zipfile.ZipFile(permanent_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    readme_path = os.path.join(temp_process_dir, "README.txt")
                    zipf.write(readme_path, "README.txt")
                    logger.info("已添加README.txt到ZIP文件")
            
            # 创建临时版本作为备份
            shutil.copy2(permanent_zip_path, zip_path)
            
            # 也将原始星表文件复制到持久化目录，便于后续检查
            try:
                catalog_filename = os.path.basename(catalog_path)
                shutil.copy2(catalog_path, os.path.join(permanent_task_dir, catalog_filename))
                logger.info(f"原始星表已保存到持久化目录: {permanent_task_dir}")
            except Exception as e:
                logger.error(f"复制原始星表到持久化目录时出错: {e}")
            
            # 更新任务状态为完成
            with tasks_lock:
                tasks[task_id]['status'] = 'completed'
                tasks[task_id]['end_time'] = datetime.now().isoformat()
                tasks[task_id]['zip_path'] = permanent_zip_path  # 使用持久化路径
                tasks[task_id]['stats'] = stats
                tasks[task_id]['progress'] = 100
                tasks[task_id]['message'] = "处理完成"
        else:
            # 如果ZIP文件创建失败，手动创建一个包含基本信息的ZIP文件
            logger.warning(f"警告: ZIP文件创建失败，尝试手动创建")
            try:
                with zipfile.ZipFile(permanent_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    # 创建一个简单的README文件
                    readme_content = f"Euclid Q1 裁剪结果 - 任务ID: {task_id}\n"
                    readme_content += f"处理时间: {datetime.now().isoformat()}\n"
                    readme_content += f"星表包含 {len(catalog)} 个源\n"
                    
                    # 将README内容写入ZIP
                    zipf.writestr("README.txt", readme_content)
                    logger.info("已手动创建包含README的ZIP文件")
                
                # 更新任务状态为完成
                with tasks_lock:
                    tasks[task_id]['status'] = 'completed'
                    tasks[task_id]['end_time'] = datetime.now().isoformat()
                    tasks[task_id]['zip_path'] = permanent_zip_path
                    tasks[task_id]['stats'] = stats
                    tasks[task_id]['progress'] = 100
                    tasks[task_id]['message'] = "处理完成（基本信息）"
            except Exception as e:
                raise Exception(f"创建ZIP文件失败: {str(e)}")
        
        # 清理临时目录
        shutil.rmtree(task_output_dir, ignore_errors=True)
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"任务处理失败: {error_msg}")
        with tasks_lock:
            tasks[task_id]['status'] = 'failed'
            tasks[task_id]['error'] = error_msg
            tasks[task_id]['end_time'] = datetime.now().isoformat()

# ================== Utility Functions ==================
def read_catalog():
    """Read source catalog and extract object IDs and class labels."""
    cat_files = list(CATALOG_DIR.glob("*.fits")) + list(CATALOG_DIR.glob("*.csv"))
    if not cat_files:
        raise FileNotFoundError(f"No catalog files found in {CATALOG_DIR}")

    for f in cat_files:
        try:
            if f.suffix == ".fits":
                with fits.open(str(f)) as hdul:
                    data = hdul[1].data
                    df = pd.DataFrame(np.array(data).byteswap().newbyteorder())
            else:
                df = pd.read_csv(f)

            for col in ["CLASS", "TYPE", "OBJ_TYPE", "SOURCE_TYPE"]:
                if col in df.columns:
                    label_col = col
                    break
            else:
                continue

            for id_col in ["SOURCE_ID", "OBJECT_ID", "ID"]:
                if id_col in df.columns:
                    key_col = id_col
                    break
            else:
                continue

            logger.info(f"✅ Using catalog: {f.name}, ID={key_col}, Label={label_col}")
            df = df[[key_col, label_col]].dropna()
            df[label_col] = df[label_col].astype(str)
            return df.rename(columns={key_col: "id", label_col: "cls"})
        except Exception as e:
            logger.warning(f"⚠️ Failed to read {f}: {e}")
    raise RuntimeError("No valid catalog file found")

def fits_to_pil(path: Path) -> Image.Image:
    """Convert FITS to normalized PIL Image."""
    with fits.open(str(path)) as hdul:
        data = hdul[0].data
        if data is None:
            raise ValueError(f"Empty FITS: {path}")
        if data.ndim > 2:
            data = data[0]
        data = np.nan_to_num(data)
        data -= np.min(data)
        if np.max(data) > 0:
            data /= np.max(data)
        data = (data * 255).astype(np.uint8)
        return Image.fromarray(data).convert("RGB")

# ================== Dataset Definition ==================
class EuclidCatalogDataset(Dataset):
    def __init__(self, catalog_df, img_dirs, transform=None):
        self.df = catalog_df
        self.img_dirs = img_dirs
        self.transform = transform
        self.classes = sorted(self.df["cls"].unique().tolist())
        self.class_to_idx = {c: i for i, c in enumerate(self.classes)}
        self.samples = []
        for _, row in self.df.iterrows():
            sid, label = str(row["id"]), row["cls"]
            found = None
            for d in img_dirs:
                for ext in [".fits", ".fits.fz"]:
                    files = list(d.rglob(f"*{sid}{ext}"))
                    if files:
                        found = files[0]; break
                if found: break
            if found:
                self.samples.append((found, self.class_to_idx[label]))
        if not self.samples:
            raise RuntimeError("No matching images found in Euclid data")

    def __len__(self): return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        img = fits_to_pil(path)
        if self.transform: img = self.transform(img)
        return img, label

# ================== Flask API Routes ==================
@app.route('/')
def index():
    """主页"""
    return render_template('index_Euclid_legacy.html')

@app.route("/api/upload_file", methods=["POST"])
def upload_file():
    """上传星表文件到服务器"""
    try:
        # 获取上传的文件
        if 'catalog' not in request.files:
            return jsonify({'error': '没有找到星表文件'}), 400
        
        file = request.files['catalog']
        if file.filename == '':
            return jsonify({'error': '没有选择文件'}), 400
        
        # 检查文件类型
        if not file.filename.endswith('.fits'):
            return jsonify({'error': '只支持FITS格式的星表文件'}), 400
        
        # 生成临时ID用于标识上传的文件
        temp_id = str(uuid.uuid4())
        temp_filename = f"{temp_id}.fits"
        file_path = os.path.join(UPLOAD_DIR, temp_filename)
        
        # 保存文件
        file.save(file_path)
        
        # 验证文件是否成功保存（处理Linux文件系统延迟问题）
        retry_count = 0
        max_retries = 5
        file_exists = False
        
        while retry_count < max_retries:
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                file_exists = True
                break
            retry_count += 1
            time.sleep(0.2)  # 等待200ms后重试
        
        if not file_exists:
            raise Exception("文件保存失败，无法验证文件是否成功写入磁盘")
        
        file_size = os.path.getsize(file_path)
        logger.info(f"文件已上传: {file.filename} 保存为临时文件 {temp_filename} (大小: {file_size} bytes)")
        
        return jsonify({
            'success': True,
            'filename': file.filename,  # 返回原始文件名给前端显示
            'temp_id': temp_id,  # 返回临时ID用于后续任务提交
            'file_size': file_size,
            'message': '文件上传成功'
        })
    except Exception as e:
        logger.error(f"文件上传失败: {str(e)}")
        return jsonify({'error': f'文件上传失败: {str(e)}'}), 500

@app.route('/api/submit_task', methods=['POST'])
def submit_task():
    """提交任务处理"""
    try:
        # 获取临时文件ID和原始文件名
        temp_id = request.form.get('temp_id')
        original_filename = request.form.get('filename')
        
        # 详细记录收到的参数，帮助调试
        logger.info(f"收到任务提交请求 - temp_id: {temp_id}, filename: {original_filename}")
        logger.debug(f"表单参数: {dict(request.form)}")
        
        if not temp_id:
            return jsonify({'error': '临时文件ID不能为空'}), 400
        
        # 检查临时文件是否存在（增加重试机制处理文件系统延迟）
        temp_file_path = os.path.join(UPLOAD_DIR, f"{temp_id}.fits")
        retry_count = 0
        max_retries = 3
        file_exists = False
        
        while retry_count < max_retries:
            if os.path.exists(temp_file_path) and os.path.getsize(temp_file_path) > 0:
                file_exists = True
                break
            retry_count += 1
            time.sleep(0.3)  # 等待300ms后重试
        
        if not file_exists:
            # 尝试查找目录中所有文件，帮助调试
            try:
                files_in_upload = os.listdir(UPLOAD_DIR)
                logger.debug(f"上传目录中的文件: {[f for f in files_in_upload if f.startswith(temp_id[:8])][:5]}")
            except Exception as e:
                logger.error(f"无法读取上传目录: {e}")
                logger.debug(f"UPLOAD_DIR路径: {UPLOAD_DIR}")
            
            return jsonify({'error': '临时文件不存在，请重新上传文件', 'temp_id': temp_id}), 404
        
        # 生成任务ID
        task_id = str(uuid.uuid4())
        
        # 将临时文件重命名为任务ID
        catalog_path = os.path.join(UPLOAD_DIR, f"{task_id}.fits")
        os.rename(temp_file_path, catalog_path)
        
        logger.info(f"文件已重命名: 从 {temp_id}.fits 重命名为 {task_id}.fits")
        
        # 获取配置参数 - 支持批量裁剪不同文件类型
        # 支持的文件类型: BGSUB, CATALOG-PSF, FLAG, BGMOD, RMS, GRID-PSF
        # 支持的仪器: VIS, NISP, DECAM, HSC, GPC, MEGACAM
        # 支持的波段根据仪器不同: NISP(NIR-Y, NIR-J, NIR-H), DECAM(DES-G, DES-R, DES-I, DES-Z), VIS(VIS), HSC(WISHES-G, WISHES-Z), GPC(PANSTARRS-I), MEGACAM(CFIS-U, CFIS-R)
        
        # 获取并验证文件类型
        supported_file_types = ['BGSUB', 'CATALOG-PSF', 'FLAG', 'BGMOD', 'RMS', 'GRID-PSF']
        requested_file_types = request.form.getlist('file_types[]')
        file_types = [ft for ft in requested_file_types if ft in supported_file_types]
        if not file_types:
            file_types = ['BGSUB', 'CATALOG-PSF']  # 默认文件类型
        
        # 获取并验证仪器类型
        supported_instruments = ['VIS', 'NISP', 'DECAM', 'HSC', 'GPC', 'MEGACAM']
        requested_instruments = request.form.getlist('instruments[]')
        instruments = [inst for inst in requested_instruments if inst in supported_instruments]
        # 根据需求，只能选择一种仪器，取第一个或使用默认值
        if len(instruments) > 1:
            instruments = [instruments[0]]  # 只使用第一个选中的仪器
            logger.info(f"根据需求，只能选择一种仪器，已选择: {instruments[0]}")
        if not instruments:
            instruments = ['VIS']  # 默认仪器类型
        
        # 获取并验证波段 - 适配单选模式
        band_map = {
            'NISP': ['NIR-Y', 'NIR-J', 'NIR-H'],
            'DECAM': ['DES-G', 'DES-R', 'DES-I', 'DES-Z'],
            'VIS': ['VIS'],
            'HSC': ['WISHES-G', 'WISHES-Z'],
            'GPC': ['PANSTARRS-I'],
            'MEGACAM': ['CFIS-U', 'CFIS-R']
        }
        bands = None
        if 'bands[]' in request.form:
            requested_bands = request.form.getlist('bands[]')
            # 由于是单选，我们只取第一个选中的波段
            if requested_bands:
                bands = requested_bands[0]  # 使用单个波段字符串而不是列表
                # 验证波段是否与选中的仪器匹配
                valid_bands = []
                for instrument in instruments:
                    if instrument in band_map and bands in band_map[instrument]:
                        valid_bands.append(bands)
                if valid_bands:
                    bands = valid_bands[0]  # 确保只有一个有效波段
                else:
                    bands = None
        
        # 构建配置字典
        config = {
            'file_types': file_types,
            'ra_col': request.form.get('ra_col', 'RA'),
            'dec_col': request.form.get('dec_col', 'DEC'),
            'target_id_col': request.form.get('target_id_col', 'TARGETID'),
            'size': int(request.form.get('size', 128)),
            'instruments': instruments,
            'band': bands,  # 改为单数形式，适应单选模式
            'n_workers': min(int(request.form.get('n_workers', 4)), 16)  # 限制最大工作进程数为16
        }
        
        # 初始化任务状态
        with tasks_lock:
            tasks[task_id] = {
                'id': task_id,
                'filename': original_filename,  # 保存原始文件名用于显示
                'status': 'queued',
                'created_at': datetime.now().isoformat(),
                'config': config
            }
        
        # 异步执行任务
        executor = ThreadPoolExecutor(max_workers=1)
        executor.submit(process_task, task_id, catalog_path, config)
        
        return jsonify({
            'task_id': task_id,
            'status_url': url_for('get_task_status', task_id=task_id)
        })
        
    except Exception as e:
        logger.error(f"提交任务失败: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """获取任务状态，包含进度信息"""
    with tasks_lock:
        if task_id not in tasks:
            return jsonify({'error': '任务不存在'}), 404
        
        task = tasks[task_id].copy()  # 创建副本以避免锁期间的修改
    
    response = {
        'id': task['id'],
        'filename': task['filename'],
        'status': task['status'],
        'created_at': task['created_at'],
        'progress': task.get('progress', 0)  # 添加进度信息
    }
    
    if 'start_time' in task:
        response['start_time'] = task['start_time']
    
    if 'end_time' in task:
        response['end_time'] = task['end_time']
    
    if 'message' in task:
        response['message'] = task['message']
    
    if 'error' in task:
        response['error'] = task['error']
    
    # 为处理中的任务添加详细的进度信息
    if task['status'] == 'processing':
        # 使用message字段作为主要进度显示（包含进度条格式）
        # 如果有processing_details，也添加到响应中
        if 'processing_details' in task:
            response['processing_details'] = task['processing_details']
        elif 'stats' in task:
            response['stats_preview'] = {
                'total_sources': task['stats'].get('total_sources', 0),
                'cached_sources': task['stats'].get('cached_sources', 0),
                'permanent_cached': task['stats'].get('permanent_cached', 0)
            }
    
    # 为已完成的任务添加下载链接和完整统计信息
    if task['status'] == 'completed' and 'zip_path' in task:
        response['download_url'] = url_for('download_result', task_id=task_id)
        # 确保stats是可JSON序列化的
        if 'stats' in task:
            response['stats'] = task['stats']
    
    return jsonify(response)

@app.route('/api/download/<task_id>', methods=['GET'])
def download_result(task_id):
    """下载任务结果"""
    try:
        with tasks_lock:
            if task_id not in tasks or tasks[task_id]['status'] != 'completed':
                return jsonify({'error': '任务未完成或不存在'}), 404
            
            zip_path = tasks[task_id].get('zip_path')
            if not zip_path:
                return jsonify({'error': '任务结果路径未设置'}), 500
        
        if not os.path.exists(zip_path):
            return jsonify({'error': '结果文件不存在: ' + zip_path}), 404
        
        logger.info(f"正在提供下载: {zip_path}")
        return send_file(zip_path, as_attachment=True, download_name=f"cutout_result_{task_id}.zip")
    except Exception as e:
        logger.error(f"下载错误: {str(e)}")
        return jsonify({'error': '下载失败: ' + str(e)}), 500

@app.route('/api/tasks', methods=['GET'])
def list_tasks():
    """列出所有任务"""
    with tasks_lock:
        # 创建任务列表的副本
        tasks_list = list(tasks.values())
    
    # 处理每个任务，添加下载链接
    result = []
    for t in tasks_list:
        task_data = {
            'id': t['id'],
            'filename': t.get('filename', '未知文件'),
            'status': t['status'],
            'created_at': t['created_at'],
            'start_time': t.get('start_time'),
            'end_time': t.get('end_time')
        }
        # 为已完成的任务添加下载链接
        if t['status'] == 'completed' and 'zip_path' in t:
            task_data['download_url'] = url_for('download_result', task_id=t['id'])
        result.append(task_data)
    
    return jsonify(result)

@app.route("/health")
def api_health():
    return jsonify({"device": str(DEVICE)})

# ================== Main ==================
if __name__ == "__main__":
    # 确保templates目录存在
    templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
    os.makedirs(templates_dir, exist_ok=True)
    
    # 重置任务列表为空
    reset_tasks()
    
    logger.info(f"🚀 Starting EuclidQ1 Service on {DEVICE}")
    app.run(host="0.0.0.0", port=5000)
