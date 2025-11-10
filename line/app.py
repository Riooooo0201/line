from flask import Flask, request, abort, render_template, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime
import os
import logging
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import sys

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====================
# 🔑 LINE Bot 設定
# ====================
LINE_CHANNEL_SECRET = "fe96005b5be582c09f6d8c192fbf6b06" 
LINE_CHANNEL_ACCESS_TOKEN = "SvfdWFWa+A+nrZTLlBGMPKoEf6fN/miJg93DS0benY9Jb/6fjh/5PBD/Wrz/RKJ2TLGoHhDUuGOee4hQJsHQ5gwZP0elG25qUPp8pzVbZCvhsoY7UHjki20EeU/fN7xhy07hUuJQIpdQtojgbp/pPAdB04t89/1O/w1cDnyilFU=" 

# ====================
# 🔑 LINE Login 設定 (LIFF認証用)
# ====================
LINE_LOGIN_CHANNEL_ID = "2008454581"
LINE_LOGIN_CHANNEL_SECRET = "f4999a4e5ed14c42c92871ffc5a01d39"

# ====================
# FlaskとLINE SDKの初期化
# ====================
app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN) 
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ====================
# 💾 Firestore設定 (サービスアカウント認証)
# ====================
# 🚨 ダウンロードしたJSONファイル名に合わせて修正してください 🚨
FIREBASE_KEY_FILENAME = 'firebase-key.json' 
FIREBASE_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), FIREBASE_KEY_FILENAME)

try:
    # Firebaseの初期化（プロジェクトID satounikikun を設定）
    if not firebase_admin._apps:
        cred = credentials.Certificate(FIREBASE_KEY_PATH)
        firebase_admin.initialize_app(cred, {'projectId': 'satounikikun'}) 
    
    db = firestore.client()
    logger.info("Firebase and Firestore connection successful.")
except Exception as e:
    logger.error(f"Firestore initialization failed: {e}")
    db = None 

# ====================
# 🌐 Webhook エンドポイント
# ====================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        abort(500)

    return 'OK'

# ====================
# ヘルパー関数: ユーザー作成
# ====================
def create_user_if_not_exists(user_id):
    """
    指定されたuser_idのユーザーが存在しない場合、LINEプロファイルから情報を取得してFirestoreに作成します。
    """
    try:
        # 'users' コレクションでユーザーを検索
        user_ref = db.collection('users').where('line_user_id', '==', user_id).limit(1)
        docs = user_ref.stream()
        
        # ユーザーが存在しない場合のみ作成
        if not any(docs):
            # LINE APIからユーザープロファイルを取得
            profile = line_bot_api.get_profile(user_id)
            display_name = profile.display_name
            
            # 新しいユーザーデータを準備
            new_user_data = {
                'line_user_id': user_id,
                'name': display_name,
                'is_registered': False, # 初期登録ステータス
                'role': 'student', # デフォルトの役割を 'student' に設定
                'created_at': datetime.now().isoformat()
            }
            
            # 'users' コレクションに新しいドキュメントを追加
            db.collection('users').add(new_user_data)
            logger.info(f"New user created: {display_name} (ID: {user_id}) with role 'student'")
            
    except Exception as e:
        logger.error(f"Failed to create or check user: {e}")

# ====================
# 💬 メッセージ処理ハンドラー
# ====================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text
    
    if db:
        # ユーザーが存在しない場合は作成する
        create_user_if_not_exists(user_id)
        
        # 1. Firestoreにメッセージを保存する
        try:
            message_data = {
                'user_id': user_id,
                'message_text': user_message,
                'timestamp': datetime.now().isoformat() 
            }
            
            # 'line_messages' コレクションに新しいドキュメントを追加する
            db.collection('line_messages').add(message_data)
            
            logger.info("Message saved to Firestore successfully.")
            reply_text = f"メッセージを受け付けました。\n内容：{user_message}" 
            
        except Exception as e:
            logger.error(f"FATAL: Firestore save failed with error: {e}")
            reply_text = "エラー：データの保存に失敗しました。管理者に連絡してください。"
    else:
        # DB接続失敗時のフォールバック処理
        reply_text = "エラー：サーバーがデータベースに接続できませんでした。"
        
    # 2. ユーザーに応答を返す
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# ====================
# 👤 ユーザーAPIエンドポイント
# ====================
@app.route('/api/user', methods=['POST'])
def update_user_profile():
    print("--- /api/user POST endpoint was hit ---", file=sys.stderr)
    if not db:
        print("Firestore is not initialized.", file=sys.stderr)
        return jsonify({"status": "error", "message": "Database connection failed"}), 500

    data = request.get_json()
    id_token = data.get('idToken')
    name = data.get('name')
    school = data.get('school')
    class_name = data.get('class') # 'class'はPythonの予約語なので'class_name'を使用

    if not id_token:
        return jsonify({"status": "error", "message": "ID Token is missing"}), 400

    # IDトークンを検証
    try:
        res = requests.post('https://api.line.me/oauth2/v2.1/verify', data={
            'id_token': id_token,
            'client_id': LINE_LOGIN_CHANNEL_ID
        })
        
        if res.status_code != 200:
            print(f"ID Token verification failed with status {res.status_code}: {res.text}", file=sys.stderr)
            return jsonify({"status": "error", "message": "ID Token verification failed"}), 401

        token_info = res.json()
        line_user_id = token_info.get('sub') # 'sub'がLINEユーザーID
        
        if not line_user_id:
            print("Verified ID Token does not contain 'sub' (user ID).", file=sys.stderr)
            return jsonify({"status": "error", "message": "Invalid ID Token (no user ID)"}), 401

    except requests.exceptions.RequestException as e:
        print(f"Request to LINE verify endpoint failed: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": "ID Token verification failed"}), 401
    except Exception as e:
        print(f"Error processing ID Token: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": "Internal server error during token processing"}), 500

    # Firestoreでユーザー情報を更新
    try:
        users_ref = db.collection('users')
        query = users_ref.where('line_user_id', '==', line_user_id).limit(1)
        docs = query.stream()
        
        user_doc_id = None
        for doc in docs:
            user_doc_id = doc.id
            break
        
        if user_doc_id:
            update_data = {
                'name': name,
                'school': school,
                'class_name': class_name,
                'is_registered': True,
                'updated_at': datetime.now().isoformat()
            }
            db.collection('users').document(user_doc_id).update(update_data)
            return jsonify({"status": "success", "message": "Profile updated successfully"}), 200
        else:
            new_user_data = {
                'line_user_id': line_user_id,
                'name': name,
                'school': school,
                'class_name': class_name,
                'is_registered': True,
                'role': 'student',
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            db.collection('users').add(new_user_data)
            return jsonify({"status": "success", "message": "Profile created successfully"}), 201

    except Exception as e:
        print(f"Error updating user profile in Firestore: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": "Failed to update profile"}), 500

@app.route('/api/user', methods=['GET'])
def get_user_profile():
    if not db:
        print("Firestore is not initialized.", file=sys.stderr)
        return jsonify({"status": "error", "message": "Database connection failed"}), 500

    id_token = request.headers.get('Authorization')
    if id_token and id_token.startswith('Bearer '):
        id_token = id_token.split(' ')[1]
    else:
        return jsonify({"status": "error", "message": "Authorization header with ID Token is missing"}), 400

    # IDトークンを検証
    try:
        res = requests.post('https://api.line.me/oauth2/v2.1/verify', data={
            'id_token': id_token,
            'client_id': LINE_LOGIN_CHANNEL_ID
        })

        if res.status_code != 200:
            print(f"ID Token verification failed with status {res.status_code}: {res.text}", file=sys.stderr)
            return jsonify({"status": "error", "message": "ID Token verification failed"}), 401

        token_info = res.json()
        line_user_id = token_info.get('sub')
        
        if not line_user_id:
            print("Verified ID Token does not contain 'sub' (user ID).", file=sys.stderr)
            return jsonify({"status": "error", "message": "Invalid ID Token (no user ID)"}), 401

    except requests.exceptions.RequestException as e:
        print(f"Request to LINE verify endpoint failed: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": "ID Token verification failed"}), 401
    except Exception as e:
        print(f"Error processing ID Token: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": "Internal server error during token processing"}), 500

    # Firestoreからユーザー情報を取得
    try:
        users_ref = db.collection('users')
        query = users_ref.where('line_user_id', '==', line_user_id).limit(1)
        docs = query.stream()
        
        user_data = None
        for doc in docs:
            user_data = doc.to_dict()
            break
        
        if user_data:
            response_data = {
                'name': user_data.get('name', ''),
                'school': user_data.get('school', ''),
                'class': user_data.get('class_name', ''),
                'is_registered': user_data.get('is_registered', False),
                'role': user_data.get('role', 'student')
            }
            return jsonify({"status": "success", "data": response_data}), 200
        else:
            return jsonify({"status": "error", "message": "User profile not found"}), 404

    except Exception as e:
        print(f"Error fetching user profile from Firestore: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": "Failed to fetch profile"}), 500

# ====================
# 🌐 Webページ表示ルート
# ====================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/posts')
def posts():
    return render_template('posts.html')

@app.route('/mypage')
def mypage():
    return render_template('mypage.html')

@app.route('/rules')

def rules():

    return render_template('rules.html')