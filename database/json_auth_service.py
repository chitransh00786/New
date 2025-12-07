"""
JSON-based Authentication Service
Replaces PostgreSQL-based authentication with file-based JSON storage
"""
import json
import os
import secrets
from datetime import datetime, time, timedelta
import time
from typing import Optional, Dict
import uuid
import bcrypt
import jwt
from enum import Enum
import threading

class UserRole(Enum):
    ADMIN = "admin"
    DJ = "dj"
    USER = "user"

class JSONAuthService:
    def __init__(self, json_file="json/auth_clients.json"):
        self.json_file = json_file
        self.lock = threading.Lock()
        self.jwt_secret = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-this")
        self.jwt_algorithm = "HS256"
        self.access_token_expire_minutes = 60
        
        # In-memory store for verification tokens: {token: user_email, expiration_time}
        # NOTE: In a real app, replace this with a database (Redis, SQL, etc.)
        self.VERIFICATION_TOKENS = {}
        self.TOKEN_EXPIRY_SECONDS = 86400  # 24 hours
        
    def _load_users(self) -> Dict:
        """Load users from JSON file"""
        try:
            with open(self.json_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            print(f"Error decoding {self.json_file}")
            return {}
    
    def _save_users(self, users: Dict):
        """Save users to JSON file"""
        with self.lock:
            with open(self.json_file, 'w') as f:
                json.dump(users, f, indent=4)
    
    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt"""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get user by username - prioritizes entries with password_hash"""
        users = self._load_users()
        matches = []
        
        for client_id, user_data in users.items():
            if user_data.get('client_name') == username or user_data.get('username') == username:
                matches.append({**user_data, 'id': client_id})
        
        if not matches:
            return None
        
        # Prioritize entries with password_hash (full user accounts)
        for match in matches:
            if 'password_hash' in match:
                return match
        
        # Fall back to first match if no password hash found
        return matches[0]
    
    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """Get user by email"""
        users = self._load_users()
        for client_id, user_data in users.items():
            if user_data.get('email') == email:
                return {**user_data, 'id': client_id}
        return None
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        """Get user by ID (client_id)"""
        users = self._load_users()
        if user_id in users:
            return {**users[user_id], 'id': user_id}
        return None
    
    def get_user_by_api_key(self, api_key: str) -> Optional[Dict]:
        """Get user by API key"""
        users = self._load_users()
        for client_id, user_data in users.items():
            if user_data.get('client_auth_key') == api_key:
                return {**user_data, 'id': client_id}
        return None
    
    def authenticate_user(self, username: str, password: str) -> Optional[Dict]:
        """Authenticate user with username and password"""
        user = self.get_user_by_username(username)
        if not user:
            return None
        
        if not user.get('password_hash'):
            return None
        
        if not self.verify_password(password, user['password_hash']):
            return None
        
        # Update last login
        self.update_last_login(user['id'])
        return user
    
    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None):
        """Create JWT access token"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire_minutes)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, self.jwt_secret, algorithm=self.jwt_algorithm)
        return encoded_jwt
    
    def verify_token(self, token: str) -> Optional[Dict]:
        """Verify JWT token and return payload"""
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=[self.jwt_algorithm])
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.PyJWTError:
            return None
    
    def _generate_next_user_id(self, users: Dict) -> int:
        """Generate next unique user ID"""
        if not users:
            return 1
        
        # Find the maximum numeric ID
        max_id = 0
        for client_id in users.keys():
            try:
                # Try to convert to int, skip non-numeric IDs
                num_id = int(client_id)
                if num_id > max_id:
                    max_id = num_id
            except (ValueError, TypeError):
                # Skip non-numeric IDs (legacy usernames)
                continue
        
        return max_id + 1
    
    def create_user(self, username: str, email: str, password: str, role: str = "user") -> Dict:
        """Create a new user with an initial verification token."""
        users = self._load_users()
        # ... (Username/Email existence checks remain here) ...
        
        client_id = str(self._generate_next_user_id(users))
        password_hash = self.hash_password(password)
        
        # Generate the necessary token data
        raw_token = str(uuid.uuid4())
        token_expiry_time = time.time() + self.TOKEN_EXPIRY_SECONDS
        
        user_data = {
            "client_id": client_id,
            "client_name": username,
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "client_auth_key": None,
            "role": role,
            "is_DJ": role in ["dj", "admin"],
            "client_description": f"{role.capitalize()} user",
            "is_verified": False,
            
            # 🔑 NEW PERSISTENT TOKEN STORAGE
            "verification_token": raw_token, 
            "token_expires_at": token_expiry_time,
            
            "created_at": datetime.utcnow().isoformat(),
            "last_login": None
        }
        
        users[client_id] = user_data
        self._save_users(users)
        
        return {**user_data, 'id': client_id}
    
    def update_last_login(self, user_id: str):
        """Update user's last login timestamp"""
        users = self._load_users()
        if user_id in users:
            users[user_id]['last_login'] = datetime.utcnow().isoformat()
            self._save_users(users)
    
    def update_user_role(self, user_id: str, new_role: str) -> bool:
        """Update user's role"""
        users = self._load_users()
        if user_id in users:
            users[user_id]['role'] = new_role
            users[user_id]['is_DJ'] = new_role in ["dj", "admin"]
            self._save_users(users)
            return True
        return False
    
    def reset_password(self, user_id: str, new_password: str) -> bool:
        """Reset user's password"""
        users = self._load_users()
        if user_id in users:
            users[user_id]['password_hash'] = self.hash_password(new_password)
            self._save_users(users)
            return True
        return False
    
    def regenerate_api_key(self, user_id: str) -> Optional[str]:
        """Regenerate user's API key"""
        users = self._load_users()
        if user_id in users:
            new_api_key = secrets.token_urlsafe(32)
            users[user_id]['client_auth_key'] = new_api_key
            self._save_users(users)
            return new_api_key
        return None
    
    def delete_user(self, user_id: str) -> bool:
        """Delete a user"""
        users = self._load_users()
        if user_id in users:
            del users[user_id]
            self._save_users(users)
            return True
        return False

    def update_username(self, user_id: str, new_username: str) -> bool:
        """Update user's username"""
        users = self._load_users()
        if user_id in users:
            users[user_id]["client_name"] = new_username
            users[user_id]["username"] = new_username
            self._save_users(users)
            return True
        return False

    def update_password(self, user_id: str, new_password: str) -> bool:
        """Update user's password"""
        users = self._load_users()
        if user_id in users:
            users[user_id]["password_hash"] = self.hash_password(new_password)
            self._save_users(users)
            return True
        return False
    
    def get_all_users(self) -> list:
        """Get all users"""
        users = self._load_users()
        return [
            {**user_data, 'id': client_id} 
            for client_id, user_data in users.items()
            if 'password_hash' in user_data  # Only return users with passwords (not API-only clients)
        ]
    
    def grant_dj_role(self, user_id: str) -> bool:
        """Grant DJ role to user"""
        return self.update_user_role(user_id, "dj")
    
    def revoke_dj_role(self, user_id: str) -> bool:
        """Revoke DJ role from user"""
        return self.update_user_role(user_id, "user")
    
    def generate_verification_token(self, email: str) -> str:
        """
        Retrieves the existing, active verification token from the user's data. 
        If expired, a new token is created, saved to the user record, and returned.
        """
        user = self.get_user_by_email(email)
        current_time = datetime.now().timestamp()
        
        if not user:
            raise ValueError("User not found for token generation.")
            
        existing_token = user.get("verification_token")
        expiry_time = user.get("token_expires_at", 0) # Default to 0 if key missing

        # 1. Check if the existing token is still active
        if existing_token and expiry_time > current_time:
            # Optional: Refresh the expiry time on every request (keeps link alive)
            user["token_expires_at"] = current_time + self.TOKEN_EXPIRY_SECONDS
            self.set_user_data(user["client_id"], user) # Saves the updated expiry
            return existing_token
        
        # 2. Token is missing or expired: Generate and save a new one
        raw_token = str(uuid.uuid4())
        token_expiry_time = current_time + self.TOKEN_EXPIRY_SECONDS
        
        user["verification_token"] = raw_token
        user["token_expires_at"] = token_expiry_time
        
        self.set_user_data(user["client_id"], user) # Saves the new token data
        
        return raw_token
    
    # NOTE: You will need a helper function in your service to save updated user data:
    def set_user_data(self, user_id: str, user_data: Dict):
        data = self._load_users()
        if user_id in data:
            data[user_id] = user_data
            self._save_users(data)

    def verify_and_delete_token(self, token: str) -> bool:
        """Finds the user whose token matches, verifies it, sets status, and deletes token data."""
        
        # 1. We must iterate to find the user that owns this specific token
        data = self._load_users()
        user_list = data.values() # Iterate through the user objects
        
        for user in user_list:
            if user.get("verification_token") == token:
                
                # 2. Check Expiry
                expiry_time = user.get("token_expires_at", 0)
                if time.time() > expiry_time:
                    # Token found but expired. Clear the token data.
                    user["verification_token"] = None
                    user["token_expires_at"] = None
                    self._save_users(data)
                    return False
                    
                # 3. Success: Set verified status and clear token data
                user["is_verified"] = True
                user["verification_token"] = None
                user["token_expires_at"] = None
                
                self._save_users(data)
                return True

        # Token not found in any user record
        return False

# Global instance
json_auth_service = JSONAuthService()
