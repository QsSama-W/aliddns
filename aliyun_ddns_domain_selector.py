import sys
import json
import re
import os
import requests
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QLineEdit, 
                            QPushButton, QComboBox, QTextEdit, QGroupBox, 
                            QMessageBox, QFrame, QTableWidget, QTableWidgetItem,
                            QHeaderView, QHBoxLayout, QVBoxLayout, QDialog)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QEvent, QUrl
from PyQt5.QtGui import QFont, QIcon, QPalette, QColor, QDesktopServices, QPixmap

# 阿里云SDK相关导入
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.acs_exception.exceptions import ClientException, ServerException
from aliyunsdkalidns.request.v20150109 import (DescribeDomainsRequest, 
                                              DescribeSubDomainRecordsRequest,
                                              DescribeDomainRecordsRequest,
                                              UpdateDomainRecordRequest,
                                              AddDomainRecordRequest,
                                              DeleteDomainRecordRequest,
                                              SetDomainRecordStatusRequest)

CURRENT_VERSION = "v2.0.0"
RELEASES_URL = "https://api.github.com/repos/QsSama-W/aliddns/releases/latest"

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_PATH = os.path.join(APP_DIR, 'AccessKey.json')


class DomainUpdateEvent(QEvent):
    EventType = QEvent.Type(QEvent.registerEventType())
    
    def __init__(self, domains):
        super().__init__(DomainUpdateEvent.EventType)
        self.domains = domains


class UpdateCheckThread(QThread):
    update_available = pyqtSignal(str)
    no_update = pyqtSignal()
    check_failed = pyqtSignal(str)
    
    def run(self):
        try:
            response = requests.get(RELEASES_URL, timeout=10)
            if response.status_code != 200:
                self.check_failed.emit(f"请求失败，状态码: {response.status_code}")
                return
                
            release_info = response.json()
            latest_version = release_info.get('tag_name', '')
            
            if not re.match(r'^v\d+\.\d+\.\d+$', latest_version):
                self.check_failed.emit(f"获取的版本格式无效: {latest_version}")
                return
                
            if self.is_new_version(latest_version, CURRENT_VERSION):
                self.update_available.emit(latest_version)
            else:
                self.no_update.emit()
                
        except requests.exceptions.Timeout:
            self.check_failed.emit("连接超时，请检查网络")
        except requests.exceptions.RequestException as e:
            self.check_failed.emit(f"网络请求错误: {str(e)}")
        except Exception as e:
            self.check_failed.emit(f"检查更新失败: {str(e)}")
    
    def is_new_version(self, latest, current):
        latest_numbers = list(map(int, latest[1:].split('.')))
        current_numbers = list(map(int, current[1:].split('.')))
        
        for l, c in zip(latest_numbers, current_numbers):
            if l > c:
                return True
            elif l < c:
                return False
        return False


class WorkerThread(QThread):
    signal = pyqtSignal(str, bool)
    domain_signal = pyqtSignal(list)
    records_signal = pyqtSignal(list)
    
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        
    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.signal.emit(result, True)
        except Exception as e:
            self.signal.emit(f"操作失败: {str(e)}", False)


class AliyunDNSClient:
    def __init__(self, access_key_id, access_key_secret, region_id="cn-hangzhou"):
        self.client = self._init_client(access_key_id, access_key_secret, region_id)
        
    def _init_client(self, access_key_id, access_key_secret, region_id):
        try:
            return AcsClient(ak=access_key_id, secret=access_key_secret, region_id=region_id)
        except TypeError:
            return AcsClient(access_key_id=access_key_id, access_key_secret=access_key_secret, region_id=region_id)
    
    def get_domains(self):
        try:
            request = DescribeDomainsRequest.DescribeDomainsRequest()
            request.set_accept_format('json')
            request.set_PageSize(100)
            
            response = self.client.do_action_with_exception(request)
            response_data = json.loads(response)
            
            if response_data.get('TotalCount', 0) > 0:
                return [domain['DomainName'] for domain in response_data['Domains']['Domain']]
            return []
        except (ClientException, ServerException) as e:
            raise Exception(f"获取域名列表失败: {str(e)}")
    
    def get_record_id(self, main_domain, sub_domain, record_type="A"):
        try:
            full_sub_domain = f"{sub_domain}.{main_domain}" if sub_domain != '@' else main_domain
            request = DescribeSubDomainRecordsRequest.DescribeSubDomainRecordsRequest()
            request.set_accept_format('json')
            request.set_SubDomain(full_sub_domain)
            request.set_Type(record_type)
            
            response = self.client.do_action_with_exception(request)
            response_data = json.loads(response)
            
            if response_data['TotalCount'] > 0:
                return response_data['DomainRecords']['Record'][0]['RecordId']
            return None
        except (ClientException, ServerException) as e:
            raise Exception(f"查询解析记录失败: {str(e)}")
    
    def get_domain_records(self, main_domain):
        try:
            request = DescribeDomainRecordsRequest.DescribeDomainRecordsRequest()
            request.set_accept_format('json')
            request.set_DomainName(main_domain)
            request.set_PageSize(100)
            
            response = self.client.do_action_with_exception(request)
            response_data = json.loads(response)
            
            records = []
            if response_data.get('TotalCount', 0) > 0:
                for record in response_data['DomainRecords']['Record']:
                    if record['Type'] in ['A', 'AAAA']:
                        rr = record['RR']
                        full_domain = f"{rr}.{main_domain}" if rr != '@' else main_domain
                        records.append({
                            'full_domain': full_domain,
                            'rr': rr,
                            'type': record['Type'],
                            'value': record['Value'],
                            'record_id': record['RecordId'],
                            'status': record['Status']
                        })
            return records
        except (ClientException, ServerException) as e:
            raise Exception(f"获取解析记录失败: {str(e)}")
    
    def update_record(self, record_id, main_domain, sub_domain, ip_address, record_type="A"):
        try:
            request = UpdateDomainRecordRequest.UpdateDomainRecordRequest()
            request.set_accept_format('json')
            request.set_RecordId(record_id)
            request.set_RR(sub_domain)
            request.set_Type(record_type)
            request.set_Value(ip_address)
            
            response = self.client.do_action_with_exception(request)
            return json.loads(response)
        except (ClientException, ServerException) as e:
            raise Exception(f"更新解析记录失败: {str(e)}")
    
    def add_record(self, main_domain, sub_domain, ip_address, record_type="A"):
        try:
            request = AddDomainRecordRequest.AddDomainRecordRequest()
            request.set_accept_format('json')
            request.set_DomainName(main_domain)
            request.set_RR(sub_domain)
            request.set_Type(record_type)
            request.set_Value(ip_address)
            
            response = self.client.do_action_with_exception(request)
            return json.loads(response)
        except (ClientException, ServerException) as e:
            raise Exception(f"添加解析记录失败: {str(e)}")
    
    def set_record_status(self, record_id, status):
        try:
            request = SetDomainRecordStatusRequest.SetDomainRecordStatusRequest()
            request.set_accept_format('json')
            request.set_RecordId(record_id)
            request.set_Status(status)
            
            response = self.client.do_action_with_exception(request)
            return json.loads(response)
        except (ClientException, ServerException) as e:
            raise Exception(f"设置解析状态失败: {str(e)}")
    
    def delete_record(self, record_id):
        try:
            request = DeleteDomainRecordRequest.DeleteDomainRecordRequest()
            request.set_accept_format('json')
            request.set_RecordId(record_id)
            
            response = self.client.do_action_with_exception(request)
            return json.loads(response)
        except (ClientException, ServerException) as e:
            raise Exception(f"删除解析记录失败: {str(e)}")


class ConfigDialog(QDialog):
    """账号配置弹窗"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setWindowTitle("账号配置")
        self.setFixedSize(400, 280)
        self.init_ui()
        self.load_config()
        
        # 设置字体
        font = QFont("Microsoft YaHei", 9)
        self.setFont(font)
        
        # 设置样式
        self.setStyleSheet("""
            QDialog {
                background-color: #F9FAFB;
            }
            QLabel {
                color: #374151;
                font-weight: 500;
            }
            QLineEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 9pt;
                font-family: "Microsoft YaHei";
                background-color: #FAFAFA;
            }
            QLineEdit:focus {
                border-color: #F97316;
                background-color: white;
            }
        """)
    
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 20, 30, 20)
        main_layout.setSpacing(15)
        
        # AccessKey ID
        ak_id_layout = QHBoxLayout()
        ak_id_label = QLabel("AccessKey ID:")
        ak_id_label.setFixedWidth(115)
        ak_id_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.access_key_id_edit = QLineEdit()
        ak_id_layout.addWidget(ak_id_label)
        ak_id_layout.addWidget(self.access_key_id_edit)
        
        # AccessKey Secret
        ak_secret_layout = QHBoxLayout()
        ak_secret_label = QLabel("AccessKey Secret:")
        ak_secret_label.setFixedWidth(115)
        ak_secret_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.access_key_secret_edit = QLineEdit()
        self.access_key_secret_edit.setEchoMode(QLineEdit.Password)
        self.access_key_secret_edit.installEventFilter(self)
        ak_secret_layout.addWidget(ak_secret_label)
        ak_secret_layout.addWidget(self.access_key_secret_edit)
        
        # 区域ID
        region_layout = QHBoxLayout()
        region_label = QLabel("区域ID:")
        region_label.setFixedWidth(115)
        region_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.region_edit = QLineEdit("cn-hangzhou")
        region_layout.addWidget(region_label)
        region_layout.addWidget(self.region_edit)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)
        
        self.test_conn_btn = QPushButton("测试连接")
        self.test_conn_btn.setFixedSize(100, 35)
        
        self.load_config_btn = QPushButton("加载配置")
        self.load_config_btn.setFixedSize(100, 35)
        
        self.save_config_btn = QPushButton("保存配置")
        self.save_config_btn.setFixedSize(100, 35)
        
        # 按钮样式
        button_style = """
            QPushButton {
                background-color: #F97316;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 600;
                font-family: "Microsoft YaHei";
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #EA580C;
            }
            QPushButton:pressed {
                background-color: #C2410C;
            }
        """
        
        self.test_conn_btn.setStyleSheet(button_style)
        self.load_config_btn.setStyleSheet(button_style)
        self.save_config_btn.setStyleSheet(button_style)
        
        btn_layout.addWidget(self.test_conn_btn)
        btn_layout.addWidget(self.load_config_btn)
        btn_layout.addWidget(self.save_config_btn)
        
        # 添加到主布局
        main_layout.addLayout(ak_id_layout)
        main_layout.addLayout(ak_secret_layout)
        main_layout.addLayout(region_layout)
        main_layout.addLayout(btn_layout)
        
        # 连接信号槽
        self.test_conn_btn.clicked.connect(self.test_connection)
        self.load_config_btn.clicked.connect(self.load_config)
        self.save_config_btn.clicked.connect(self.save_config)
    
    def eventFilter(self, obj, event):
        if obj == self.access_key_secret_edit:
            if event.type() == QEvent.Enter:
                self.access_key_secret_edit.setEchoMode(QLineEdit.Normal)
            elif event.type() == QEvent.Leave:
                self.access_key_secret_edit.setEchoMode(QLineEdit.Password)
        return super().eventFilter(obj, event)
    
    def load_config(self):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.access_key_id_edit.setText(config.get('access_key_id', ''))
                self.access_key_secret_edit.setText(config.get('access_key_secret', ''))
                self.region_edit.setText(config.get('region_id', 'cn-hangzhou'))
                
                if self.parent:
                    self.parent.log(f"配置文件加载成功: {CONFIG_PATH}")
        except FileNotFoundError:
            if self.parent:
                self.parent.log(f"未找到配置文件: {CONFIG_PATH}", False)
        except Exception as e:
            if self.parent:
                self.parent.log(f"加载配置失败: {str(e)}", False)
    
    def save_config(self):
        try:
            config = {
                'access_key_id': self.access_key_id_edit.text().strip(),
                'access_key_secret': self.access_key_secret_edit.text().strip(),
                'region_id': self.region_edit.text().strip() or 'cn-hangzhou'
            }
            
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            
            if self.parent:
                self.parent.log(f"配置已保存到: {CONFIG_PATH}")
                self.parent.auto_load_domains()
                
            QMessageBox.information(self, "保存成功", "配置已成功保存")
        except Exception as e:
            if self.parent:
                self.parent.log(f"保存配置失败: {str(e)}", False)
            QMessageBox.warning(self, "保存失败", f"保存配置失败: {str(e)}")
    
    def test_connection(self):
        access_key_id = self.access_key_id_edit.text().strip()
        access_key_secret = self.access_key_secret_edit.text().strip()
        region_id = self.region_edit.text().strip() or 'cn-hangzhou'
        
        if not access_key_id or not access_key_secret:
            QMessageBox.warning(self, "输入错误", "请填写AccessKey ID和AccessKey Secret")
            return
        
        if self.parent:
            self.parent.log("正在测试连接...")
            self.parent.statusBar().showMessage("正在测试连接...")
        
        def test_func():
            client = AliyunDNSClient(access_key_id, access_key_secret, region_id)
            domains = client.get_domains()
            return f"连接成功，共找到 {len(domains)} 个域名"
        
        self.worker_thread = WorkerThread(test_func)
        self.worker_thread.signal.connect(self.on_test_finished)
        self.worker_thread.start()
    
    def on_test_finished(self, message, success):
        if self.parent:
            self.parent.log(message, success)
            self.parent.statusBar().showMessage(message, 5000)
        
        if success:
            QMessageBox.information(self, "测试成功", message)
        else:
            QMessageBox.warning(self, "测试失败", message)


class DNSManagerUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.dns_client = None
        self.worker_thread = None
        self.update_thread = None
        self.ensure_config_exists()
        self.init_ui()
        self.check_for_updates(show_no_update_msg=False)
    
    def safe_terminate_thread(self, thread):
        if thread and thread.isRunning():
            thread.terminate()
            thread.wait()
        
    def closeEvent(self, event):
        self.safe_terminate_thread(self.worker_thread)
        self.safe_terminate_thread(self.update_thread)
        event.accept()
        
    def ensure_config_exists(self):
        if not os.path.exists(CONFIG_PATH):
            default_config = {
                'access_key_id': '',
                'access_key_secret': '',
                'region_id': 'cn-hangzhou'
            }
            try:
                with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=2)
            except Exception as e:
                QMessageBox.warning(None, "配置文件创建失败", 
                                   f"无法创建配置文件: {str(e)}\n程序可能无法正常工作")
    
    def init_ui(self):
        # 设置字体和窗口基本属性
        self.setWindowTitle(f"域名解析设置工具(阿里云){CURRENT_VERSION}")
        self.setFixedSize(1000, 730)

        base_dir = os.path.dirname(os.path.abspath(__file__))
        images_dir = os.path.join(base_dir, "images")
        try:
            logo = QPixmap(os.path.join(images_dir, "logo.png"))
            icon = QIcon(logo)
            self.setWindowIcon(icon)
        except:
            pass

        # 全局字体设置
        base_font = QFont("Microsoft YaHei", 9)
        self.setFont(base_font)
        
        central_widget = QWidget()
        central_widget.setStyleSheet("background-color: #F9FAFB;")
        self.setCentralWidget(central_widget)
        
        # 标题区域
        title_container = QFrame(central_widget)
        title_container.setGeometry(30, 10, 940, 50)
        title_container.setStyleSheet("background-color: transparent;")
        
        title_label = QLabel("域名解析设置工具(阿里云)", title_container)
        title_font = QFont("Microsoft YaHei", 16, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        title_label.setStyleSheet("""
            color: #EA580C; 
            background-color: transparent;
        """)
        title_label.setGeometry(350, 0, 300, 50)
        
        # 右侧功能按钮 - 修复按钮显示问题
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # 账号配置按钮
        self.config_btn = QPushButton("账号配置")
        self.config_btn.setFixedSize(100, 30)
        self.config_btn.clicked.connect(self.open_config_dialog)
        
        # 检查更新按钮
        self.check_update_btn = QPushButton("检查更新")
        self.check_update_btn.setFixedSize(100, 30)
        self.check_update_btn.clicked.connect(lambda: self.check_for_updates(show_no_update_msg=True))
        
        # 按钮样式
        self.config_btn.setStyleSheet("""
            QPushButton {
                background-color: #4F46E5;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 600;
                font-family: "Microsoft YaHei";
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #4338CA;
            }
        """)
        
        self.check_update_btn.setStyleSheet("""
            QPushButton {
                background-color: #4F46E5;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 600;
                font-family: "Microsoft YaHei";
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #4338CA;
            }
        """)
        
        # 创建按钮容器并添加按钮 - 调整位置使按钮完全显示
        btn_container = QWidget(title_container)
        btn_container.setGeometry(720, 10, 220, 40)
        button_layout.addWidget(self.config_btn)
        button_layout.addWidget(self.check_update_btn)
        btn_container.setLayout(button_layout)
        
        # 分割线
        line = QFrame(central_widget)
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #FECACA; margin-bottom: 10px;")
        line.setGeometry(30, 70, 940, 2)
        
        # 按钮样式定义
        button_style = """
            QPushButton {
                background-color: #F97316;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 600;
                font-family: "Microsoft YaHei";
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #EA580C;
            }
            QPushButton:pressed {
                background-color: #C2410C;
            }
            QPushButton:disabled {
                background-color: #FED7D7;
                color: #9CA3AF;
            }
        """
        
        primary_button_style = button_style + """
            background-color: #EA580C;
            padding: 8px 16px;
        """
        
        # 域名解析设置区域
        self.domain_group = QGroupBox("域名解析设置", central_widget)
        self.domain_group.setGeometry(30, 90, 940, 200)
        self.domain_group.setStyleSheet("""
            QGroupBox {
                font-size: 11pt;
                font-weight: bold;
                color: #374151;
                font-family: "Microsoft YaHei";
                border: 1px solid #FECACA;
                border-radius: 8px;
                margin-top: 10px;
                padding: 20px 15px 15px 15px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                padding: 0 5px 0 5px;
                color: #F97316;
            }
        """)
        
        # 域名解析设置区域内容
        domain_label = QLabel("主域名:", self.domain_group)
        domain_label.setStyleSheet("color: #374151; font-weight: 500; font-size: 9pt; font-family: 'Microsoft YaHei'; background-color: transparent;")
        domain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        domain_label.setGeometry(20, 30, 70, 30)
        
        self.domain_combo = QComboBox(self.domain_group)
        self.domain_combo.setGeometry(100, 30, 500, 30)
        self.domain_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 9pt;
                font-family: "Microsoft YaHei";
                background-color: #FAFAFA;
            }
            QComboBox:focus {
                border-color: #F97316;
                background-color: white;
            }
        """)
        self.domain_combo.currentIndexChanged.connect(self.on_domain_changed)
        
        refresh_domain_btn = QPushButton("刷新域名", self.domain_group)
        refresh_domain_btn.setGeometry(620, 30, 90, 30)
        refresh_domain_btn.setStyleSheet(button_style)
        refresh_domain_btn.clicked.connect(self.refresh_domains)
        
        subdomain_label = QLabel("子域名:", self.domain_group)
        subdomain_label.setStyleSheet("color: #374151; font-weight: 500; font-size: 9pt; font-family: 'Microsoft YaHei'; background-color: transparent;")
        subdomain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        subdomain_label.setGeometry(20, 75, 70, 30)
        
        self.subdomain_edit = QLineEdit(self.domain_group)
        self.subdomain_edit.setPlaceholderText("留空表示解析主域名")
        self.subdomain_edit.setGeometry(100, 75, 200, 30)
        self.subdomain_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 9pt;
                font-family: "Microsoft YaHei";
                background-color: #FAFAFA;
            }
            QLineEdit:focus {
                border-color: #F97316;
                background-color: white;
            }
        """)
        
        ip_label = QLabel("IP地址:", self.domain_group)
        ip_label.setStyleSheet("color: #374151; font-weight: 500; font-size: 9pt; font-family: 'Microsoft YaHei'; background-color: transparent;")
        ip_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ip_label.setGeometry(330, 75, 70, 30)
        
        self.ip_edit = QLineEdit(self.domain_group)
        self.ip_edit.setPlaceholderText("支持IPV4/IPV6")
        self.ip_edit.setGeometry(410, 75, 300, 30)
        self.ip_edit.textChanged.connect(self.detect_ip_version)
        self.ip_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 9pt;
                font-family: "Microsoft YaHei";
                background-color: #FAFAFA;
            }
            QLineEdit:focus {
                border-color: #F97316;
                background-color: white;
            }
        """)
        
        self.ip_version_label = QLabel("IP版本: 未检测", self.domain_group)
        self.ip_version_label.setStyleSheet("""
            font-size: 8pt;
            color: #6B7280;
            font-family: "Microsoft YaHei";
            background-color: transparent;
        """)
        self.ip_version_label.setGeometry(410, 110, 120, 20)
        
        self.set_record_btn = QPushButton("设置解析", self.domain_group)
        self.set_record_btn.setGeometry(100, 140, 120, 35)
        self.set_record_btn.setStyleSheet(primary_button_style)
        self.set_record_btn.clicked.connect(self.set_dns_record)
        
        self.clear_btn = QPushButton("清空输入", self.domain_group)
        self.clear_btn.setGeometry(240, 140, 120, 35)
        self.clear_btn.setStyleSheet(button_style)
        self.clear_btn.clicked.connect(self.clear_inputs)
        
        # 已解析记录展示区域
        self.records_group = QGroupBox("已解析记录", central_widget)
        self.records_group.setGeometry(30, 310, 940, 230)
        self.records_group.setStyleSheet("""
            QGroupBox {
                font-size: 11pt;
                font-weight: bold;
                color: #374151;
                font-family: "Microsoft YaHei";
                border: 1px solid #FECACA;
                border-radius: 8px;
                margin-top: 10px;
                padding: 20px 15px 15px 15px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                padding: 0 5px 0 5px;
                color: #F97316;
            }
        """)
        
        # 解析记录表格
        self.records_table = QTableWidget(self.records_group)
        self.records_table.setGeometry(10, 30, 910, 180)
        self.records_table.setColumnCount(5)
        self.records_table.setHorizontalHeaderLabels(["完整域名", "记录类型", "IP地址", "状态", "操作"])
        
        self.records_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)  # 完整域名自动拉伸
        self.records_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 记录类型
        self.records_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)  # IP地址
        self.records_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 状态
        self.records_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)  # 操作
        self.records_table.setColumnWidth(4, 210)
        self.records_table.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.records_table.verticalHeader().setMinimumSectionSize(40)
        
        self.records_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #FECACA;
                border-radius: 4px;
                background-color: #FAFAFA;
                font-family: "Microsoft YaHei";
                font-size: 8.5pt;
            }
            QHeaderView::section {
                background-color: #FEE2E2;
                color: #374151;
                padding: 5px;
                border: 1px solid #FECACA;
                font-weight: bold;
                font-size: 8.5pt;
            }
            QTableWidget::item {
                padding: 5px;
                border-bottom: 1px solid #FEE2E2;
            }
            QTableWidget::item:selected {
                background-color: #FFEDD5;
                color: #C2410C;
            }
        """)
        
        # 操作日志区域
        self.log_group = QGroupBox("操作日志", central_widget)
        self.log_group.setGeometry(30, 560, 940, 120)
        self.log_group.setStyleSheet("""
            QGroupBox {
                font-size: 11pt;
                font-weight: bold;
                color: #374151;
                font-family: "Microsoft YaHei";
                border: 1px solid #FECACA;
                border-radius: 8px;
                margin-top: 10px;
                padding: 10px 15px 5px 15px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                padding: 0 5px 0 5px;
                color: #F97316;
            }
        """)
        
        self.log_text = QTextEdit(self.log_group)
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QTextEdit.WidgetWidth)
        self.log_text.setGeometry(10, 20, 910, 90)
        self.log_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                background-color: #FAFAFA;
                font-family: "Microsoft YaHei", Consolas, 'Courier New', monospace;
                font-size: 8.5pt;
                padding: 3px;
            }
            QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(249, 115, 22, 0.5);
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        
        # 状态栏
        self.statusBar().setStyleSheet("""
            background-color: #F9FAFB; 
            color: #374151; 
            border-top: 1px solid #FECACA;
            font-size: 8.5pt;
            font-family: "Microsoft YaHei";
        """)
        self.statusBar().showMessage("就绪")
        
        self.check_and_prompt_config()
        self.auto_load_domains()
    
    def open_config_dialog(self):
        """打开账号配置弹窗"""
        dialog = ConfigDialog(self)
        dialog.exec_()
    
    def check_and_prompt_config(self):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                access_key_id = config.get('access_key_id', '').strip()
                access_key_secret = config.get('access_key_secret', '').strip()
                
                if not access_key_id or not access_key_secret:
                    reply = QMessageBox.question(
                        self, "配置不完整", 
                        "检测到配置文件不完整，是否现在进行配置？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                    )
                    if reply == QMessageBox.Yes:
                        self.open_config_dialog()
        except Exception:
            pass
    
    def auto_load_domains(self):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                access_key_id = config.get('access_key_id', '').strip()
                access_key_secret = config.get('access_key_secret', '').strip()
                
                if access_key_id and access_key_secret:
                    self.log("正在加载域名列表...")
                    self.refresh_domains()
                else:
                    self.log("请点击'账号配置'按钮设置AccessKey信息", False)
        except Exception:
            self.log("请点击'账号配置'按钮设置AccessKey信息", False)
    
    def detect_ip_version(self):
        ip_address = self.ip_edit.text().strip()
        if not ip_address:
            self.ip_version_label.setText("IP版本: 未检测")
            palette = QPalette()
            palette.setColor(QPalette.WindowText, QColor(107, 114, 128))
            self.ip_version_label.setPalette(palette)
            return
            
        if self.is_valid_ipv4(ip_address):
            self.ip_version_label.setText("IP版本: IPv4")
            palette = QPalette()
            palette.setColor(QPalette.WindowText, QColor(16, 185, 129))
            self.ip_version_label.setPalette(palette)
        elif self.is_valid_ipv6(ip_address):
            self.ip_version_label.setText("IP版本: IPv6")
            palette = QPalette()
            palette.setColor(QPalette.WindowText, QColor(59, 130, 246))
            self.ip_version_label.setPalette(palette)
        else:
            self.ip_version_label.setText("IP版本: 无效格式")
            palette = QPalette()
            palette.setColor(QPalette.WindowText, QColor(239, 68, 68))
            self.ip_version_label.setPalette(palette)
    
    def log(self, message, is_success=True):
        prefix = "[成功] " if is_success else "[错误] "
        color = "#10B981" if is_success else "#EF4444"
        self.log_text.append(f'<span style="color:{color}">{prefix}</span>{message}')
        self.log_text.moveCursor(self.log_text.textCursor().End)
        
        # 只保留最近20条日志，避免日志过多卡死
        log_count = self.log_text.document().blockCount()
        if log_count > 20:
            cursor = self.log_text.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.movePosition(cursor.NextBlock, cursor.KeepAnchor, log_count - 20)
            cursor.removeSelectedText()
            cursor.deleteChar()
            
        self.statusBar().showMessage(message, 5000)
    
    def refresh_domains(self):
        self.safe_terminate_thread(self.worker_thread)
        
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                access_key_id = config.get('access_key_id', '').strip()
                access_key_secret = config.get('access_key_secret', '').strip()
                region_id = config.get('region_id', 'cn-hangzhou').strip() or 'cn-hangzhou'
                
                if not access_key_id or not access_key_secret:
                    QMessageBox.warning(self, "配置不完整", "请先完成账号配置")
                    self.open_config_dialog()
                    return
                
                self.log("正在刷新域名列表...")
                self.domain_combo.clear()
                
                self.worker_thread = WorkerThread(self._fetch_domains, access_key_id, access_key_secret, region_id)
                self.worker_thread.signal.connect(self.on_worker_finished)
                self.worker_thread.domain_signal.connect(self.update_domain_combo)
                self.worker_thread.start()
        except Exception as e:
            self.log(f"加载配置失败: {str(e)}", False)
            QMessageBox.warning(self, "配置错误", f"加载配置失败: {str(e)}\n请重新配置")
            self.open_config_dialog()
    
    def _fetch_domains(self, access_key_id, access_key_secret, region_id):
        self.dns_client = AliyunDNSClient(access_key_id, access_key_secret, region_id)
        domains = self.dns_client.get_domains()
        
        self.worker_thread.domain_signal.emit(domains)
        
        if not domains:
            return "未找到任何域名，请先在阿里云控制台添加域名"
        return f"成功加载 {len(domains)} 个域名"
    
    def update_domain_combo(self, domains):
        self.domain_combo.clear()
        self.domain_combo.addItems(domains)
    
    def on_domain_changed(self, index):
        if index < 0:
            return
            
        self.safe_terminate_thread(self.worker_thread)
        
        main_domain = self.domain_combo.currentText()
        if not main_domain:
            return
            
        self.log(f"正在加载 {main_domain} 的解析记录...")
        self.records_table.setRowCount(0)
        
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                access_key_id = config.get('access_key_id', '').strip()
                access_key_secret = config.get('access_key_secret', '').strip()
                region_id = config.get('region_id', 'cn-hangzhou').strip() or 'cn-hangzhou'
                
                def fetch_records_func():
                    if not self.dns_client:
                        self.dns_client = AliyunDNSClient(access_key_id, access_key_secret, region_id)
                    
                    records = self.dns_client.get_domain_records(main_domain)
                    self.worker_thread.records_signal.emit(records)
                    return f"成功加载 {main_domain} 的 {len(records)} 条解析记录"
                
                self.worker_thread = WorkerThread(fetch_records_func)
                self.worker_thread.signal.connect(self.on_worker_finished)
                self.worker_thread.records_signal.connect(self.update_records_table)
                self.worker_thread.start()
        except Exception as e:
            self.log(f"加载配置失败: {str(e)}", False)
    
    def update_records_table(self, records):
        self.records_table.setRowCount(len(records))
        
        for row, record in enumerate(records):
            # 完整域名
            full_domain_item = QTableWidgetItem(record['full_domain'])
            full_domain_item.setFlags(full_domain_item.flags() & ~Qt.ItemIsEditable)
            
            # 记录类型
            type_item = QTableWidgetItem(record['type'])
            type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
            if record['type'] == 'A':
                type_item.setForeground(QColor(16, 185, 129))
            else:
                type_item.setForeground(QColor(59, 130, 246))
            
            # IP地址
            ip_item = QTableWidgetItem(record['value'])
            ip_item.setFlags(ip_item.flags() & ~Qt.ItemIsEditable)
            
            # 状态
            status_item = QTableWidgetItem("启用" if record['status'] == 'ENABLE' else "暂停")
            status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
            if record['status'] == 'ENABLE':
                status_item.setForeground(QColor(16, 185, 129))
            else:
                status_item.setForeground(QColor(239, 68, 68))
            
            # 操作按钮
            btn_widget = QWidget()
            hbox = QHBoxLayout(btn_widget)
            hbox.setContentsMargins(0, 0, 0, 0)
            
            
            if record['status'] == 'ENABLE':
                pause_btn = QPushButton("暂停")
                pause_btn.setFixedSize(60, 20)
                pause_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #F59E0B;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        padding: 2px 8px;
                        font-size: 8pt;
                    }
                    QPushButton:hover {
                        background-color: #D97706;
                    }
                """)
                pause_btn.clicked.connect(lambda checked, rid=record['record_id']: self.toggle_record_status(rid, 'disable'))
                hbox.addWidget(pause_btn)
            else:
                enable_btn = QPushButton("启用")
                enable_btn.setFixedSize(60, 20)
                enable_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #10B981;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        padding: 2px 8px;
                        font-size: 8pt;
                    }
                    QPushButton:hover {
                        background-color: #059669;
                    }
                """)
                enable_btn.clicked.connect(lambda checked, rid=record['record_id']: self.toggle_record_status(rid, 'ENABLE'))
                hbox.addWidget(enable_btn)
            
            delete_btn = QPushButton("删除")
            delete_btn.setFixedSize(60, 20)
            delete_btn.setStyleSheet("""
                QPushButton {
                    background-color: #EF4444;
                    color: white;
                    border: none;
                    border-radius: 3px;
                    padding: 2px 8px;
                    font-size: 8pt;
                }
                QPushButton:hover {
                    background-color: #DC2626;
                }
            """)
            delete_btn.clicked.connect(lambda checked, rid=record['record_id'], domain=record['full_domain']: self.delete_record(rid, domain))
            hbox.addWidget(delete_btn)
            
            btn_widget.setLayout(hbox)
            
            # 设置表格内容
            self.records_table.setItem(row, 0, full_domain_item)
            self.records_table.setItem(row, 1, type_item)
            self.records_table.setItem(row, 2, ip_item)
            self.records_table.setItem(row, 3, status_item)
            self.records_table.setCellWidget(row, 4, btn_widget)
    
    def toggle_record_status(self, record_id, status):
        self.safe_terminate_thread(self.worker_thread)
        
        action = "暂停" if status == 'disable' else "启用"
        self.log(f"正在{action}解析记录...")
        
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                access_key_id = config.get('access_key_id', '').strip()
                access_key_secret = config.get('access_key_secret', '').strip()
                region_id = config.get('region_id', 'cn-hangzhou').strip() or 'cn-hangzhou'
                
                def toggle_func():
                    if not self.dns_client:
                        self.dns_client = AliyunDNSClient(access_key_id, access_key_secret, region_id)
                    
                    self.dns_client.set_record_status(record_id, status)
                    main_domain = self.domain_combo.currentText()
                    records = self.dns_client.get_domain_records(main_domain)
                    self.worker_thread.records_signal.emit(records)
                    return f"解析记录已成功{action}"
                
                self.worker_thread = WorkerThread(toggle_func)
                self.worker_thread.signal.connect(self.on_worker_finished)
                self.worker_thread.records_signal.connect(self.update_records_table)
                self.worker_thread.start()
        except Exception as e:
            self.log(f"加载配置失败: {str(e)}", False)
    
    def delete_record(self, record_id, domain):
        first_confirm = QMessageBox.question(
            self, "确认删除", 
            f"确定要删除解析记录 {domain} 吗？\n此操作不可恢复！",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if first_confirm != QMessageBox.Yes:
            return
            
        second_confirm = QMessageBox.question(
            self, "再次确认", 
            f"请再次确认删除解析记录 {domain}？\n这是最后一次确认！",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if second_confirm != QMessageBox.Yes:
            return
        
        self.safe_terminate_thread(self.worker_thread)
        self.log(f"正在删除解析记录 {domain}...")
        
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                access_key_id = config.get('access_key_id', '').strip()
                access_key_secret = config.get('access_key_secret', '').strip()
                region_id = config.get('region_id', 'cn-hangzhou').strip() or 'cn-hangzhou'
                
                def delete_func():
                    if not self.dns_client:
                        self.dns_client = AliyunDNSClient(access_key_id, access_key_secret, region_id)
                    
                    self.dns_client.delete_record(record_id)
                    main_domain = self.domain_combo.currentText()
                    records = self.dns_client.get_domain_records(main_domain)
                    self.worker_thread.records_signal.emit(records)
                    return f"解析记录 {domain} 已成功删除"
                
                self.worker_thread = WorkerThread(delete_func)
                self.worker_thread.signal.connect(self.on_worker_finished)
                self.worker_thread.records_signal.connect(self.update_records_table)
                self.worker_thread.start()
        except Exception as e:
            self.log(f"加载配置失败: {str(e)}", False)
    
    def on_worker_finished(self, message, success):
        self.log(message, success)
    
    def is_valid_ipv4(self, ip):
        pattern = re.compile(r'^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$')
        return pattern.match(ip) is not None
    
    def is_valid_ipv6(self, ip):
        pattern = re.compile(r'^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$')
        return pattern.match(ip) is not None
    
    def set_dns_record(self):
        main_domain = self.domain_combo.currentText()
        sub_domain = self.subdomain_edit.text().strip() or '@'
        ip_address = self.ip_edit.text().strip()
        
        if not main_domain:
            QMessageBox.warning(self, "输入错误", "请选择主域名")
            return
            
        if not ip_address:
            QMessageBox.warning(self, "输入错误", "请输入IP地址")
            return
            
        record_type = "A" if self.is_valid_ipv4(ip_address) else "AAAA" if self.is_valid_ipv6(ip_address) else None
        if not record_type:
            QMessageBox.warning(self, "输入错误", "请输入有效的IPv4或IPv6地址")
            return
        
        self.safe_terminate_thread(self.worker_thread)
        self.log(f"正在设置 {sub_domain}.{main_domain} 的解析记录...")
        
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                access_key_id = config.get('access_key_id', '').strip()
                access_key_secret = config.get('access_key_secret', '').strip()
                region_id = config.get('region_id', 'cn-hangzhou').strip() or 'cn-hangzhou'
                
                def set_record_func():
                    if not self.dns_client:
                        self.dns_client = AliyunDNSClient(access_key_id, access_key_secret, region_id)
                    
                    record_id = self.dns_client.get_record_id(main_domain, sub_domain, record_type)
                    
                    if record_id:
                        self.dns_client.update_record(record_id, main_domain, sub_domain, ip_address, record_type)
                        result = f"已更新 {sub_domain}.{main_domain} 的{record_type}记录为 {ip_address}"
                    else:
                        self.dns_client.add_record(main_domain, sub_domain, ip_address, record_type)
                        result = f"已添加 {sub_domain}.{main_domain} 的{record_type}记录为 {ip_address}"
                    
                    records = self.dns_client.get_domain_records(main_domain)
                    self.worker_thread.records_signal.emit(records)
                    return result
                
                self.worker_thread = WorkerThread(set_record_func)
                self.worker_thread.signal.connect(self.on_worker_finished)
                self.worker_thread.records_signal.connect(self.update_records_table)
                self.worker_thread.start()
        except Exception as e:
            self.log(f"加载配置失败: {str(e)}", False)
    
    def clear_inputs(self):
        self.subdomain_edit.clear()
        self.ip_edit.clear()
        self.log("已清空输入")
    
    def check_for_updates(self, show_no_update_msg):
        self.safe_terminate_thread(self.update_thread)
        self.update_thread = UpdateCheckThread()
        self.update_thread.update_available.connect(self.on_update_available)
        self.update_thread.no_update.connect(lambda: self.on_no_update(show_no_update_msg))
        self.update_thread.check_failed.connect(self.on_update_check_failed)
        self.update_thread.start()
    
    def on_update_available(self, latest_version):
        self.log(f"发现新版本: {latest_version} (当前版本: {CURRENT_VERSION})")
        
        reply = QMessageBox.question(
            self, "发现新版本",
            f"检测到新版本 {latest_version}，当前版本为 {CURRENT_VERSION}。\n是否前往下载页面？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl("https://github.com/QsSama-W/aliddns/releases"))
    
    def on_no_update(self, show_msg):
        """没有更新时只在日志中显示，不弹窗"""
        self.log(f"当前已是最新版本: {CURRENT_VERSION}")
    
    def on_update_check_failed(self, error_msg):
        self.log(f"检查更新失败: {error_msg}", False)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei")
    app.setFont(font)
    
    window = DNSManagerUI()
    window.show()
    sys.exit(app.exec_())
