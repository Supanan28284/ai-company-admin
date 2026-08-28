// ============================================
// Google Apps Script สำหรับเชื่อมต่อกับเว็บ
// Deploy as Web App และใส่ URL ใน Environment Variables
// ============================================

function doGet(e) {
  return doPost(e);
}

function doPost(e) {
  try {
    const action = e.parameter.action || (e.postData ? JSON.parse(e.postData.contents).action : null);
    
    if (action === "get_employees") {
      return getEmployees();
    } else if (action === "save_employee") {
      const data = e.postData ? JSON.parse(e.postData.contents) : e.parameter;
      return saveEmployee(data);
    } else if (action === "delete_employee") {
      const data = e.postData ? JSON.parse(e.postData.contents) : e.parameter;
      return deleteEmployee(data);
    } else if (action === "get_admins") {
      return getAdmins();
    } else if (action === "save_admin") {
      const data = e.postData ? JSON.parse(e.postData.contents) : e.parameter;
      return saveAdmin(data);
    } else if (action === "delete_admin") {
      const data = e.postData ? JSON.parse(e.postData.contents) : e.parameter;
      return deleteAdmin(data);
    } else if (action === "get_chats") {
      return getChats();
    } else if (action === "save_chat") {
      const data = e.postData ? JSON.parse(e.postData.contents) : e.parameter;
      return saveChat(data);
    } else if (action === "get_customers") {
      return getCustomers();
    } else {
      return ContentService.createTextOutput(JSON.stringify({error: "Unknown action"}))
        .setMimeType(ContentService.MimeType.JSON);
    }
  } catch (error) {
    Logger.log("Error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================
// ฟังก์ชัน EMPLOYEES (พนักงาน)
// ============================================

function getEmployees() {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    const sheet = spreadsheet.getSheetByName("Employees");
    
    if (!sheet) {
      Logger.log("Employees sheet not found, creating it...");
      createEmployeesSheet();
      return ContentService.createTextOutput(JSON.stringify([["ID", "Username", "Password Hash", "Full Name", "Role", "Status"]]))
        .setMimeType(ContentService.MimeType.JSON);
    }
    
    const range = sheet.getDataRange();
    const values = range.getValues();
    
    return ContentService.createTextOutput(JSON.stringify(values))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log("getEmployees error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function saveEmployee(data) {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    let sheet = spreadsheet.getSheetByName("Employees");
    
    if (!sheet) {
      createEmployeesSheet();
      sheet = spreadsheet.getSheetByName("Employees");
    }
    
    const employee_id = data.employee_id;
    const username = data.username;
    const password_hash = data.password_hash;
    const full_name = data.full_name;
    const role = data.role;
    const status = data.status;
    
    const allValues = sheet.getDataRange().getValues();
    let found = false;
    
    // ค้นหาแถวที่มี employee_id เดียวกัน และอัปเดต
    for (let i = 1; i < allValues.length; i++) {
      if (allValues[i][0] == employee_id) {
        sheet.getRange(i + 1, 1, 1, 6).setValues([[employee_id, username, password_hash, full_name, role, status]]);
        found = true;
        break;
      }
    }
    
    // ถ้าไม่เจอ ให้เพิ่มแถวใหม่
    if (!found) {
      sheet.appendRow([employee_id, username, password_hash, full_name, role, status]);
    }
    
    return ContentService.createTextOutput(JSON.stringify({success: true, employee_id: employee_id}))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log("saveEmployee error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function deleteEmployee(data) {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    const sheet = spreadsheet.getSheetByName("Employees");
    
    if (!sheet) {
      return ContentService.createTextOutput(JSON.stringify({error: "Sheet not found"}))
        .setMimeType(ContentService.MimeType.JSON);
    }
    
    const employee_id = data.employee_id;
    const allValues = sheet.getDataRange().getValues();
    
    // ค้นหาและลบแถวที่มี employee_id เดียวกัน
    for (let i = allValues.length - 1; i >= 1; i--) {
      if (allValues[i][0] == employee_id) {
        sheet.deleteRow(i + 1);
        break;
      }
    }
    
    return ContentService.createTextOutput(JSON.stringify({success: true}))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log("deleteEmployee error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function createEmployeesSheet() {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    const sheet = spreadsheet.insertSheet("Employees");
    
    // ตั้งหัวคอลัมน์
    sheet.appendRow(["ID", "Username", "Password Hash", "Full Name", "Role", "Status"]);
    
    // จัดรูปแบบ
    const headerRange = sheet.getRange(1, 1, 1, 6);
    headerRange.setBackground("#e2e8f0");
    headerRange.setFontWeight("bold");
    headerRange.setFontColor("#1e293b");
    
    // ปรับความกว้างของคอลัมน์
    sheet.setColumnWidth(1, 60);
    sheet.setColumnWidth(2, 120);
    sheet.setColumnWidth(3, 300);
    sheet.setColumnWidth(4, 150);
    sheet.setColumnWidth(5, 120);
    sheet.setColumnWidth(6, 100);
    
    return sheet;
  } catch (error) {
    Logger.log("createEmployeesSheet error: " + error.toString());
  }
}

// ============================================
// ฟังก์ชัน ADMINS (พนักงาน AI)
// ============================================

function getAdmins() {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    const sheet = spreadsheet.getSheetByName("Admin_Config");
    
    const range = sheet.getDataRange();
    const values = range.getValues();
    
    return ContentService.createTextOutput(JSON.stringify(values))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log("getAdmins error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function saveAdmin(data) {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    const sheet = spreadsheet.getSheetByName("Admin_Config");
    
    const admin_id = data.admin_id;
    const name = data.name;
    const company = data.company;
    const gender = data.gender;
    const channels = data.channels;
    const keywords = data.keywords;
    const system_prompt = data.system_prompt;
    const required_data_fields = data.required_data_fields || "";
    const design_style = data.design_style || "";
    const material_specs = data.material_specs || "";
    const portfolio_link = data.portfolio_link || "";
    
    const allValues = sheet.getDataRange().getValues();
    let found = false;
    
    for (let i = 1; i < allValues.length; i++) {
      if (allValues[i][0] == admin_id) {
        sheet.getRange(i + 1, 1, 1, 10).setValues([[admin_id, name, company, channels, keywords, system_prompt, gender, required_data_fields, design_style, material_specs]]);
        found = true;
        break;
      }
    }
    
    if (!found) {
      sheet.appendRow([admin_id, name, company, channels, keywords, system_prompt, gender, required_data_fields, design_style, material_specs]);
    }
    
    return ContentService.createTextOutput(JSON.stringify({success: true, admin_id: admin_id}))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log("saveAdmin error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function deleteAdmin(data) {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    const sheet = spreadsheet.getSheetByName("Admin_Config");
    
    const admin_id = data.admin_id;
    const allValues = sheet.getDataRange().getValues();
    
    for (let i = allValues.length - 1; i >= 1; i--) {
      if (allValues[i][0] == admin_id) {
        sheet.deleteRow(i + 1);
        break;
      }
    }
    
    return ContentService.createTextOutput(JSON.stringify({success: true}))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log("deleteAdmin error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================
// ฟังก์ชัน CHATS (บันทึกแชท)
// ============================================

function getChats() {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    const sheet = spreadsheet.getSheetByName("Chat_Logs");
    
    const range = sheet.getDataRange();
    const values = range.getValues();
    
    return ContentService.createTextOutput(JSON.stringify(values))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log("getChats error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function saveChat(data) {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    let sheet = spreadsheet.getSheetByName("Chat_Logs");
    
    if (!sheet) {
      sheet = spreadsheet.insertSheet("Chat_Logs");
      sheet.appendRow(["Timestamp", "Customer ID", "Customer Name", "Message", "Sender Type"]);
    }
    
    const timestamp = new Date().toISOString();
    const customer_id = data.customer_id;
    const customer_name = data.customer_name;
    const message = data.message;
    const sender_type = data.sender_type;
    
    sheet.appendRow([timestamp, customer_id, customer_name, message, sender_type]);
    
    return ContentService.createTextOutput(JSON.stringify({success: true}))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log("saveChat error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================
// ฟังก์ชัน CUSTOMERS (ลูกค้า)
// ============================================

function getCustomers() {
  try {
    const spreadsheet = SpreadsheetApp.openById("1StP6PBDw7wnPyqGiz2zbvHCWDnZimG9r4zWllhjexS8");
    const sheet = spreadsheet.getSheetByName("Customer_Data");
    
    const range = sheet.getDataRange();
    const values = range.getValues();
    
    return ContentService.createTextOutput(JSON.stringify(values))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log("getCustomers error: " + error.toString());
    return ContentService.createTextOutput(JSON.stringify({error: error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
