# Euclid 图像裁剪服务 - 星表格式详细说明

## 支持的文件格式

本服务仅支持**FITS（Flexible Image Transport System）**格式的星表文件。FITS是天文学领域标准的数据交换格式，可同时存储表格数据和图像数据。

## 必需的列信息

为了成功处理天体图像裁剪，星表文件必须包含以下关键列：

### 1. 赤经（RA）

- **格式**：十进制度数（decimal degrees）
- **示例值**：150.123456
- **范围**：0° 至 360°
- **精度建议**：至少保留5位小数以确保定位精度
- **注意**：不支持时分秒（HH:MM:SS）格式，必须提前转换为十进制度数

### 2. 赤纬（DEC）

- **格式**：十进制度数（decimal degrees）
- **示例值**：2.345678 或 -3.456789
- **范围**：-90° 至 +90°
- **精度建议**：至少保留5位小数以确保定位精度
- **注意**：不支持度分秒（DD:MM:SS）格式，必须提前转换为十进制度数

## 推荐的列信息

### 1. object_id（目标唯一标识符）

- **格式**：整数或字符串
- **作用**：提供天体的唯一标识符，用于波段缓存匹配和结果文件命名
- **重要性**：**object_id是星表中最重要的列之一**，包含object_id可以大幅提高缓存命中率，显著加速处理速度，并且确保结果文件能够正确匹配到对应的天体目标
- **命名建议**：使用简洁、唯一的标识符，避免特殊字符
- **注意**：系统默认使用列名为`object_id`，如果使用其他列名，需要在上传星表时指定唯一索引列

### 2. OBJECTID（基于坐标的目标标识符）

OBJECTID是另一种基于天体坐标自动生成的唯一标识符，其生成规则如下：

#### 生成规则
通过保留`right_ascension`的整数部分+小数点后七位文本+`declination`整数部分+小数点后七位文本，组合成一个18位或19位数字的文本。如果`declination`为负数，则在OBJECTID开头加上负号。

#### Python实现代码
```python
def generate_object_id(ra, dec):
    """
    基于赤经(RA)和赤纬(DEC)生成OBJECTID
    
    参数:
    ra: float - 赤经（十进制度数）
    dec: float - 赤纬（十进制度数）
    
    返回:
    str - 生成的OBJECTID
    """
    # 处理赤经：保留整数+小数点后7位
    ra_str = f"{ra:010.7f}"  # 确保赤经部分为10位字符（整数+小数+小数点）
    ra_part = ra_str.replace(".", "")  # 移除小数点
    
    # 处理赤纬：保留整数+小数点后7位
    dec_abs = abs(dec)
    dec_str = f"{dec_abs:09.7f}"  # 确保赤纬部分为9位字符（整数+小数+小数点）
    dec_part = dec_str.replace(".", "")  # 移除小数点
    
    # 组合生成OBJECTID
    if dec < 0:
        object_id = f"-{ra_part}{dec_part}"
    else:
        object_id = f"{ra_part}{dec_part}"
    
    return object_id

# 使用示例
ra = 150.123456789
dec = -2.345678901
object_id = generate_object_id(ra, dec)
print(f"RA: {ra}, DEC: {dec}, OBJECTID: {object_id}")
# 输出示例: RA: 150.123456789, DEC: -2.345678901, OBJECTID: -1501234568023456789
```

#### 格式特点
- **长度**：18位（赤纬为正时）或19位（赤纬为负时）
- **精度**：保留坐标的10位有效数字（赤经）和9位有效数字（赤纬）
- **唯一性**：在实际应用中，这种精度足以确保每个天体获得唯一的OBJECTID
- **可追溯性**：可以通过OBJECTID反向还原出原始坐标信息

#### 应用场景
- 当星表中没有现成的TARGETID时，可作为替代标识符
- 用于跨数据集的天体匹配和关联
- 作为缓存文件的命名依据，确保文件命名的一致性

### 2. 其他有用列（可选）

- **SOURCE_TYPE**：天体类型（星系、恒星等）
- **MAG**：星等信息
- **REDSHIFT**：红移值（如果已知）
- **PRIORITY**：处理优先级

## 星表格式转换指南

如果您的星表不是FITS格式或坐标不是十进制度数，可以使用以下工具进行转换：

### 从CSV/TXT转换为FITS

使用Astropy库：

```python
from astropy.table import Table
import pandas as pd

# 读取CSV文件
df = pd.read_csv('your_catalog.csv')

# 转换为Astropy Table
table = Table.from_pandas(df)

# 写入FITS文件
table.write('your_catalog.fits', format='fits')
```

### 坐标格式转换

将时分秒（HH:MM:SS）或度分秒（DD:MM:SS）转换为十进制度数：

```python
from astropy.coordinates import SkyCoord
from astropy import units as u

# 示例：将RA=10:30:45, DEC=+2:45:30转换为十进制度数
ra_str = '10h30m45s'
dec_str = '+2d45m30s'

# 创建SkyCoord对象
coord = SkyCoord(ra=ra_str, dec=dec_str, frame='icrs')

# 获取十进制度数
ra_deg = coord.ra.deg
dec_deg = coord.dec.deg
```

## 星表结构示例

以下是一个符合要求的FITS星表结构示例（以表格形式展示）：

| 列名 | 数据类型 | 示例值 | 说明 |
|------|---------|--------|------|
| object_id | INTEGER | 123456789 | 天体唯一标识符 |
| RA | FLOAT64 | 150.123456 | 赤经（十进制度数） |
| DEC | FLOAT64 | 2.345678 | 赤纬（十进制度数） |
| SOURCE_TYPE | STRING | 'GALAXY' | 天体类型 |
| MAG | FLOAT32 | 22.5 | 星等 |

## 常见星表问题及解决方案

### 问题1：坐标格式不正确

**症状**：处理失败，错误信息提示坐标超出范围

**解决方案**：
- 确保所有坐标都转换为十进制度数
- 检查负值处理（特别是赤纬）
- 使用验证工具检查坐标值

### 问题2：object_id不匹配缓存

**症状**：即使有缓存文件，系统仍重新处理

**解决方案**：
- 确保星表中的object_id与缓存文件名中的ID完全匹配
- 检查是否有前导零或格式差异
- 确保缓存文件位于正确的波段子目录中

### 问题3：星表文件过大

**症状**：上传失败或处理超时

**解决方案**：
- 将大星表分成多个较小的文件（每个不超过10,000行）
- 增加服务器超时设置（如果有权限）
- 使用压缩FITS格式（.fits.gz）减少传输大小

## 批量处理优化建议

1. **为频繁处理的目标提供稳定的TARGETID**：这是提高缓存效率的关键

2. **预处理坐标格式**：在上传前确保所有坐标都已转换为正确的十进制格式

3. **按区域分组处理**：如果处理多个天区，考虑按区域分组提交任务，这样可以减少数据读取时间

4. **优先级排序**：对星表按优先级排序，确保重要目标优先处理

## 格式兼容性说明

本服务与以下天文数据系统的星表格式兼容：

- Euclid官方目录
- DES (Dark Energy Survey) 目录
- HSC (Hyper Suprime-Cam) 目录
- Pan-STARRS 目录

只需确保坐标已转换为十进制度数格式，并且包含必需的列名（可通过前端界面配置）。