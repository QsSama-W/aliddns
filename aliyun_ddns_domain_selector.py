import sys
import json
import re
import os
import requests
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QLineEdit, 
                            QPushButton, QComboBox, QTextEdit, QGroupBox, 
                            QMessageBox, QFrame)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QEvent, QUrl
from PyQt5.QtGui import QFont, QIcon, QPalette, QColor, QDesktopServices, QPixmap

CURRENT_VERSION = "v1.0.0"
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


class DNSManagerUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.dns_client = None
        self.config_visible = False
        self.ensure_config_exists()
        self.init_ui()
        self.check_for_updates(show_no_update_msg=False)
        
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
        self.setWindowTitle(f"域名解析设置工具(阿里云){CURRENT_VERSION}")
        self.setFixedSize(900, 520)

        base_dir = os.path.dirname(os.path.abspath(__file__))
        images_dir = os.path.join(base_dir, "images")
        logo = QPixmap(os.path.join(images_dir, "logo.png"))
        icon = QIcon(logo)
        self.setWindowIcon(icon)

        base_font = QFont("Microsoft YaHei", 10)
        self.setFont(base_font)
        
        central_widget = QWidget()
        central_widget.setStyleSheet("background-color: #F9FAFB;")
        self.setCentralWidget(central_widget)
        
        title_container = QFrame(central_widget)
        title_container.setGeometry(50, 10, 800, 40)
        title_container.setStyleSheet("background-color: transparent;")
        
        title_label = QLabel("域名解析设置工具(阿里云)", title_container)
        title_font = QFont("Microsoft YaHei", 18, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        title_label.setStyleSheet("""
            color: #EA580C; 
            margin: 10px 0;
            background-color: transparent;
        """)
        title_label.setGeometry(245, 0, 300, 50)
        
        self.config_toggle_btn = QPushButton("账号配置", title_container)
        self.config_toggle_btn.setGeometry(680, 10, 120, 30)
        self.config_toggle_btn.setStyleSheet("""
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
        """)
        self.config_toggle_btn.clicked.connect(self.toggle_config_visibility)
        
        self.check_update_btn = QPushButton("检查更新", title_container)
        self.check_update_btn.setGeometry(550, 10, 120, 30)
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
        self.check_update_btn.clicked.connect(lambda: self.check_for_updates(show_no_update_msg=True))
        
        line = QFrame(central_widget)
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #FECACA; margin-bottom: 10px;")
        line.setGeometry(50, 60, 800, 2)
        
        button_style = """
            QPushButton {
                background-color: #F97316;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 600;
                font-family: "Microsoft YaHei";
                font-size: 10pt;
            }
            QPushButton:hover {
                background-color: #EA580C;
                transform: translateY(-1px);
            }
            QPushButton:pressed {
                background-color: #C2410C;
                transform: translateY(0);
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
        
        self.domain_group = QGroupBox("域名解析设置", central_widget)
        self.domain_group.setGeometry(50, 80, 800, 200)
        self.domain_group.setStyleSheet("""
            QGroupBox {
                font-size: 12pt;
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
        
        domain_label = QLabel("主域名:", self.domain_group)
        domain_label.setStyleSheet("color: #374151; font-weight: 500; font-size: 10pt; font-family: 'Microsoft YaHei'; background-color: transparent;")
        domain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        domain_label.setGeometry(30, 30, 80, 30)
        
        self.domain_combo = QComboBox(self.domain_group)
        self.domain_combo.setGeometry(120, 30, 450, 32)
        self.domain_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
                font-family: "Microsoft YaHei";
                background-color: #FAFAFA;
            }
            QComboBox:focus {
                border-color: #F97316;
                background-color: white;
            }
            QComboBox::drop-down {
                border-left: 1px solid #FECACA;
            }
        """)
        
        refresh_domain_btn = QPushButton("刷新域名", self.domain_group)
        refresh_domain_btn.setGeometry(590, 30, 100, 32)
        refresh_domain_btn.setStyleSheet(button_style)
        refresh_domain_btn.clicked.connect(self.refresh_domains)
        
        subdomain_label = QLabel("子域名:", self.domain_group)
        subdomain_label.setStyleSheet("color: #374151; font-weight: 500; font-size: 10pt; font-family: 'Microsoft YaHei'; background-color: transparent;")
        subdomain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        subdomain_label.setGeometry(30, 75, 80, 30)
        
        self.subdomain_edit = QLineEdit(self.domain_group)
        self.subdomain_edit.setPlaceholderText("留空表示解析主域名")
        self.subdomain_edit.setGeometry(120, 75, 200, 32)
        self.subdomain_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
                font-family: "Microsoft YaHei";
                background-color: #FAFAFA;
            }
            QLineEdit:focus {
                border-color: #F97316;
                background-color: white;
            }
        """)
        
        ip_label = QLabel("IP地址:", self.domain_group)
        ip_label.setStyleSheet("color: #374151; font-weight: 500; font-size: 10pt; font-family: 'Microsoft YaHei'; background-color: transparent;")
        ip_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ip_label.setGeometry(350, 75, 80, 30)
        
        self.ip_edit = QLineEdit(self.domain_group)
        self.ip_edit.setPlaceholderText("支持IPV4/IPV6")
        self.ip_edit.setGeometry(440, 75, 250, 32)
        self.ip_edit.textChanged.connect(self.detect_ip_version)
        self.ip_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
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
            font-size: 9pt;
            color: #6B7280;
            font-family: "Microsoft YaHei";
            background-color: transparent;
        """)
        self.ip_version_label.setGeometry(610, 120, 120, 25)
        
        self.set_record_btn = QPushButton("设置解析", self.domain_group)
        self.set_record_btn.setGeometry(120, 120, 120, 36)
        self.set_record_btn.setStyleSheet(primary_button_style)
        self.set_record_btn.clicked.connect(self.set_dns_record)
        
        self.clear_btn = QPushButton("清空输入", self.domain_group)
        self.clear_btn.setGeometry(260, 120, 120, 36)
        self.clear_btn.setStyleSheet(button_style)
        self.clear_btn.clicked.connect(self.clear_inputs)
        
        self.log_group = QGroupBox("操作日志", central_widget)
        self.log_group.setGeometry(50, 300, 800, 180)
        self.log_group.setStyleSheet("""
            QGroupBox {
                font-size: 12pt;
                font-weight: bold;
                color: #374151;
                font-family: "Microsoft YaHei";
                border: 1px solid #FECACA;
                border-radius: 8px;
                margin-top: 10px;
                padding: 15px 15px 10px 15px;
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
        self.log_text.setGeometry(10, 25, 770, 130)
        self.log_text.setStyleSheet("""
            QTextEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                background-color: #FAFAFA;
                font-family: "Microsoft YaHei", Consolas, 'Courier New', monospace;
                font-size: 9pt;
                padding: 5px;
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
            QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
                display: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        
        self.config_group = QGroupBox("账号配置", central_widget)
        self.config_group.setGeometry(50, 500, 800, 160)
        self.config_group.setStyleSheet("""
            QGroupBox {
                font-size: 12pt;
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
        self.config_group.setVisible(False)
        
        label_style = "color: #374151; font-weight: 500; font-size: 10pt; font-family: 'Microsoft YaHei'; background-color: transparent;"
        
        ak_id_label = QLabel("AccessKey ID:", self.config_group)
        ak_id_label.setStyleSheet(label_style)
        ak_id_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ak_id_label.setGeometry(30, 30, 160, 30)
        
        self.access_key_id_edit = QLineEdit(self.config_group)
        self.access_key_id_edit.setGeometry(200, 30, 350, 32)
        self.access_key_id_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
                font-family: "Microsoft YaHei";
                background-color: #FAFAFA;
            }
            QLineEdit:focus {
                border-color: #F97316;
                background-color: white;
            }
        """)
        
        ak_secret_label = QLabel("AccessKey Secret:", self.config_group)
        ak_secret_label.setStyleSheet(label_style)
        ak_secret_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ak_secret_label.setGeometry(30, 70, 160, 30)
        
        self.access_key_secret_edit = QLineEdit(self.config_group)
        self.access_key_secret_edit.setEchoMode(QLineEdit.Password)
        self.access_key_secret_edit.setGeometry(200, 70, 350, 32)
        self.access_key_secret_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
                font-family: "Microsoft YaHei";
                background-color: #FAFAFA;
            }
            QLineEdit:focus {
                border-color: #F97316;
                background-color: white;
            }
        """)
        self.access_key_secret_edit.installEventFilter(self)
        
        region_label = QLabel("区域ID:", self.config_group)
        region_label.setStyleSheet(label_style)
        region_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        region_label.setGeometry(30, 110, 160, 30)
        
        self.region_edit = QLineEdit("cn-hangzhou", self.config_group)
        self.region_edit.setGeometry(200, 110, 200, 32)
        self.region_edit.setStyleSheet("""
            QLineEdit {
                border: 1px solid #FECACA;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 10pt;
                font-family: "Microsoft YaHei";
                background-color: #FAFAFA;
            }
            QLineEdit:focus {
                border-color: #F97316;
                background-color: white;
            }
        """)
        
        test_conn_btn = QPushButton("测试连接", self.config_group)
        test_conn_btn.setGeometry(630, 30, 120, 32)
        test_conn_btn.setStyleSheet(button_style)
        test_conn_btn.clicked.connect(self.test_connection)
        
        load_config_btn = QPushButton("加载配置", self.config_group)
        load_config_btn.setGeometry(630, 70, 120, 32)
        load_config_btn.setStyleSheet(button_style)
        load_config_btn.clicked.connect(self.load_config)
        
        self.save_config_btn = QPushButton("保存并加载", self.config_group)
        self.save_config_btn.setGeometry(630, 110, 120, 32)
        self.save_config_btn.setStyleSheet(button_style)
        self.save_config_btn.clicked.connect(self.save_and_hide_config)
        
        self.statusBar().setStyleSheet("""
            background-color: #F9FAFB; 
            color: #374151; 
            border-top: 1px solid #FECACA;
            font-size: 9pt;
            font-family: "Microsoft YaHei";
        """)
        self.statusBar().showMessage("就绪")
        
        self.load_config()
        self.check_and_prompt_config()
        self.auto_load_domains()
    
    def toggle_config_visibility(self):
        self.config_visible = not self.config_visible
        self.config_group.setVisible(self.config_visible)
        
        if self.config_visible:
            self.setFixedSize(900, 720)
        else:
            self.setFixedSize(900, 520)
    
    def save_and_hide_config(self):
        self.save_config()
        if self.config_visible:
            self.toggle_config_visibility()
        self.auto_load_domains()
    
    def check_and_prompt_config(self):
        access_key_id = self.access_key_id_edit.text().strip()
        access_key_secret = self.access_key_secret_edit.text().strip()
        
        if not access_key_id or not access_key_secret:
            if not self.config_visible:
                self.toggle_config_visibility()
            
            QMessageBox.information(
                self, "配置不完整", 
                "检测到配置文件不完整，请填写AccessKey信息后点击\"保存并加载\"按钮"
            )
            
            self.access_key_id_edit.setFocus()
    
    def auto_load_domains(self):
        access_key_id = self.access_key_id_edit.text().strip()
        access_key_secret = self.access_key_secret_edit.text().strip()
        
        if access_key_id and access_key_secret:
            self.log("正在加载域名列表...")
            self.refresh_domains()
        else:
            self.log("请先配置AccessKey以加载域名列表", False)
    
    def eventFilter(self, obj, event):
        if obj == self.access_key_secret_edit:
            if event.type() == QEvent.Enter:
                self.access_key_secret_edit.setEchoMode(QLineEdit.Normal)
            elif event.type() == QEvent.Leave:
                self.access_key_secret_edit.setEchoMode(QLineEdit.Password)
        return super().eventFilter(obj, event)
    
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
        self.statusBar().showMessage(message, 5000)
    
    def load_config(self):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self.access_key_id_edit.setText(config.get('access_key_id', ''))
                self.access_key_secret_edit.setText(config.get('access_key_secret', ''))
                self.region_edit.setText(config.get('region_id', 'cn-hangzhou'))
                self.log(f"配置文件加载成功: {CONFIG_PATH}")
        except FileNotFoundError:
            self.log(f"未找到配置文件: {CONFIG_PATH}", False)
            self.ensure_config_exists()
        except Exception as e:
            self.log(f"加载配置失败: {str(e)}", False)
    
    def save_config(self):
        try:
            config = {
                'access_key_id': self.access_key_id_edit.text().strip(),
                'access_key_secret': self.access_key_secret_edit.text().strip(),
                'region_id': self.region_edit.text().strip() or 'cn-hangzhou'
            }
            
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            
            self.log(f"配置已保存到: {CONFIG_PATH}")
        except Exception as e:
            self.log(f"保存配置失败: {str(e)}", False)
    
    def test_connection(self):
        access_key_id = self.access_key_id_edit.text().strip()
        access_key_secret = self.access_key_secret_edit.text().strip()
        region_id = self.region_edit.text().strip() or 'cn-hangzhou'
        
        if not access_key_id or not access_key_secret:
            QMessageBox.warning(self, "输入错误", "请填写AccessKey ID和AccessKey Secret")
            return
        
        self.log("正在测试连接...")
        self.statusBar().showMessage("正在测试连接...")
        
        def test_func():
            client = AliyunDNSClient(access_key_id, access_key_secret, region_id)
            domains = client.get_domains()
            return f"连接成功，共找到 {len(domains)} 个域名"
        
        self.worker = WorkerThread(test_func)
        self.worker.signal.connect(self.on_worker_finished)
        self.worker.start()
    
    def refresh_domains(self):
        access_key_id = self.access_key_id_edit.text().strip()
        access_key_secret = self.access_key_secret_edit.text().strip()
        region_id = self.region_edit.text().strip() or 'cn-hangzhou'
        
        if not access_key_id or not access_key_secret:
            QMessageBox.warning(self, "输入错误", "请填写AccessKey ID和AccessKey Secret")
            return
        
        self.log("正在刷新域名列表...")
        self.domain_combo.clear()
        
        self.worker = WorkerThread(self._fetch_domains)
        self.worker.signal.connect(self.on_worker_finished)
        self.worker.domain_signal.connect(self.update_domain_combo)
        self.worker.start()
    
    def _fetch_domains(self):
        access_key_id = self.access_key_id_edit.text().strip()
        access_key_secret = self.access_key_secret_edit.text().strip()
        region_id = self.region_edit.text().strip() or 'cn-hangzhou'
        
        self.dns_client = AliyunDNSClient(access_key_id, access_key_secret, region_id)
        domains = self.dns_client.get_domains()
        
        self.worker.domain_signal.emit(domains)
        
        if not domains:
            return "未找到任何域名，请先在阿里云控制台添加域名"
        return f"成功加载 {len(domains)} 个域名"
    
    def update_domain_combo(self, domains):
        self.domain_combo.clear()
        self.domain_combo.addItems(domains)
    
    def clear_inputs(self):
        self.subdomain_edit.clear()
        self.ip_edit.clear()
        self.log("已清空输入")
    
    def is_valid_ipv4(self, ip_address):
        ip_pattern = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$'
        match = re.match(ip_pattern, ip_address)
        if not match:
            return False
        return all(0 <= int(segment) <= 255 for segment in match.groups())
    
    def is_valid_ipv6(self, ip_address):
        ipv6_pattern = r'^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$|^::([0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){1}::([0-9a-fA-F]{1,4}:){0,4}[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){2}::([0-9a-fA-F]{1,4}:){0,3}[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){3}::([0-9a-fA-F]{1,4}:){0,2}[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){4}::([0-9a-fA-F]{1,4}:){0,1}[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){5}::[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){6}::$'
        return re.match(ipv6_pattern, ip_address) is not None
    
    def set_dns_record(self):
        main_domain = self.domain_combo.currentText()
        sub_domain = self.subdomain_edit.text().strip() or '@'
        ip_address = self.ip_edit.text().strip()
        
        if not main_domain:
            QMessageBox.warning(self, "输入错误", "请先选择主域名")
            return
        
        if self.is_valid_ipv4(ip_address):
            record_type = "A"
        elif self.is_valid_ipv6(ip_address):
            record_type = "AAAA"
        else:
            QMessageBox.warning(self, "输入错误", "请输入有效的IPv4或IPv6地址")
            return
        
        full_domain = f"{sub_domain}.{main_domain}" if sub_domain != '@' else main_domain
        reply = QMessageBox.question(
            self, "确认操作", 
            f"确定要将 {full_domain} 解析到 {ip_address} ({record_type}) 吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        self.log(f"正在设置 {full_domain} -> {ip_address} ({record_type}) ...")
        
        def set_record_func():
            if not self.dns_client:
                access_key_id = self.access_key_id_edit.text().strip()
                access_key_secret = self.access_key_secret_edit.text().strip()
                region_id = self.region_edit.text().strip() or 'cn-hangzhou'
                self.dns_client = AliyunDNSClient(access_key_id, access_key_secret, region_id)
            
            record_id = self.dns_client.get_record_id(main_domain, sub_domain, record_type)
            
            if record_id:
                result = self.dns_client.update_record(
                    record_id, main_domain, sub_domain, ip_address, record_type
                )
                return f"已更新解析记录: {full_domain} -> {ip_address} ({record_type}) (记录ID: {result['RecordId']})"
            else:
                result = self.dns_client.add_record(
                    main_domain, sub_domain, ip_address, record_type
                )
                return f"已添加新解析记录: {full_domain} -> {ip_address} ({record_type}) (记录ID: {result['RecordId']})"
        
        self.worker = WorkerThread(set_record_func)
        self.worker.signal.connect(self.on_worker_finished)
        self.worker.start()
    
    def on_worker_finished(self, message, is_success):
        self.log(message, is_success)
    
    def check_for_updates(self, show_no_update_msg=True):
        self.log("正在检查更新...")
        
        self.update_thread = UpdateCheckThread()
        self.update_thread.update_available.connect(self.on_update_available)
        self.update_thread.no_update.connect(lambda: self.on_no_update(show_no_update_msg))
        self.update_thread.check_failed.connect(self.on_update_failed)
        self.update_thread.start()
    
    def on_update_available(self, latest_version):
        self.log(f"发现新版本: {latest_version} (当前版本: {CURRENT_VERSION})")
        
        reply = QMessageBox.question(
            self, 
            "发现新版本", 
            f"检测到新版本 {latest_version}，当前版本为 {CURRENT_VERSION}。\n是否前往下载页面？",
            QMessageBox.Yes | QMessageBox.No, 
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl("https://github.com/QsSama-W/aliddns/releases"))
    
    def on_no_update(self, show_message):
        self.log(f"当前已是最新版本: {CURRENT_VERSION}")
        
    def on_update_failed(self, error_msg):
        self.log(f"检查更新失败: {error_msg}", False)


from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.acs_exception.exceptions import ClientException, ServerException
from aliyunsdkalidns.request.v20150109 import (
    DescribeDomainsRequest,
    DescribeSubDomainRecordsRequest,
    UpdateDomainRecordRequest,
    AddDomainRecordRequest
)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei")
    app.setFont(font)
    
    window = DNSManagerUI()
    window.show()
    sys.exit(app.exec_())
    